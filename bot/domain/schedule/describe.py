"""Scheduled-prompt schedules in plain words, for the read-back before Confirm.

The read-back is how a wrong parse gets caught: the model turns "every weekday
at 9am" into cron, and the person checks the words, not the cron. So it names
the zone every time and lists the next few runs. The run list is the real safety
net: it comes from the same `next_run_after` the runner uses, so it's right even
for an expression `describe_cron` can only fall back on.

`describe_cron` covers the shapes people ask for in chat (daily, weekdays, some
days of the week, a day of the month, hourly, a few times a day) and falls back
to showing the expression.
"""

from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

from bot.domain.schedule.cron import next_run_after, parse_iso

ZONE_LABELS = {
    "America/Los_Angeles": "Pacific",
    "America/Denver": "Mountain",
    "America/Phoenix": "Arizona",
    "America/Chicago": "Central",
    "America/New_York": "Eastern",
    "UTC": "UTC",
    "Etc/UTC": "UTC",
}

# What people type for a zone, mapped to the IANA name the schedule stores.
ZONE_ALIASES = {
    "pt": "America/Los_Angeles", "pst": "America/Los_Angeles", "pdt": "America/Los_Angeles",
    "pacific": "America/Los_Angeles",
    "mt": "America/Denver", "mst": "America/Denver", "mdt": "America/Denver",
    "mountain": "America/Denver",
    "ct": "America/Chicago", "cst": "America/Chicago", "cdt": "America/Chicago",
    "central": "America/Chicago",
    "et": "America/New_York", "est": "America/New_York", "edt": "America/New_York",
    "eastern": "America/New_York",
    "utc": "UTC", "gmt": "UTC",
}

DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
READ_BACK_RUNS = 3


def normalize_zone(tz: Optional[str], default: str) -> str:
    """An IANA zone for whatever the model passed: a name, an alias, or nothing."""
    if not tz or not tz.strip():
        return default
    tz = tz.strip()
    return ZONE_ALIASES.get(tz.lower(), tz)


def zone_label(tz: str) -> str:
    return ZONE_LABELS.get(tz, tz)


def clock(hour: int, minute: int) -> str:
    suffix = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12}:{minute:02d} {suffix}"


def ordinal(n: int) -> str:
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _ints(field: str, low: int, high: int) -> Optional[List[int]]:
    """A field of plain numbers, commas, and ranges as a sorted list; None if fancier."""
    values = set()
    for part in field.split(","):
        if part.isdigit():
            values.add(int(part))
        elif "-" in part and all(p.isdigit() for p in part.split("-", 1)):
            start, end = (int(p) for p in part.split("-", 1))
            if start > end:
                return None
            values.update(range(start, end + 1))
        else:
            return None
    if not values or min(values) < low or max(values) > high:
        return None
    return sorted(values)


def _join(words: List[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _days(field: str) -> Optional[str]:
    if field == "*":
        return "daily"
    days = _ints(field, 0, 7)
    if days is None:
        return None
    days = sorted({d % 7 for d in days})  # 7 is Sunday too
    if days == [1, 2, 3, 4, 5]:
        return "weekdays"
    if days == [0, 6]:
        return "weekends"
    if len(days) == 7:
        return "daily"
    return "every " + _join([DAY_NAMES[d] for d in days])


def describe_cron(cron: str) -> str:
    """"daily at 9:00 AM", "weekdays at 8:30 AM", ... or the expression itself."""
    fallback = f"on the cron schedule `{cron}`"
    fields = cron.split()
    if len(fields) != 5:
        return fallback
    minute_f, hour_f, dom_f, month_f, dow_f = fields
    if month_f != "*" or not minute_f.isdigit() or int(minute_f) > 59:
        return fallback
    minute = int(minute_f)

    if hour_f == "*":
        if dom_f != "*" or dow_f != "*":
            return fallback
        return "every hour, on the hour" if minute == 0 else f"every hour at {minute} past"

    hours = _ints(hour_f, 0, 23)
    if hours is None:
        return fallback
    times = "at " + _join([clock(h, minute) for h in hours])

    if dom_f != "*":
        if dow_f != "*" or not dom_f.isdigit() or not 1 <= int(dom_f) <= 31:
            return fallback
        return f"on the {ordinal(int(dom_f))} of every month {times}"

    days = _days(dow_f)
    if days is None:
        return fallback
    return f"{days} {times}"


def format_run(when: datetime, tz: str) -> str:
    local = when.astimezone(ZoneInfo(tz))
    return f"{local.strftime('%a %b')} {local.day}, {clock(local.hour, local.minute)}"


def next_runs(cron: str, tz: str, now: datetime, count: int = READ_BACK_RUNS) -> List[datetime]:
    runs = []
    after = now
    for _ in range(count):
        after = next_run_after(cron, tz, after)
        runs.append(after)
    return runs


def describe_schedule(cron: str, tz: str) -> str:
    """"daily at 9:00 AM (Pacific time)"."""
    return f"{describe_cron(cron)} ({zone_label(tz)} time)"


def read_back(cron: str, tz: str, now: datetime) -> str:
    """The schedule and its next few runs, for the person to check."""
    runs = "; ".join(format_run(r, tz) for r in next_runs(cron, tz, now))
    return f"{describe_schedule(cron, tz)} (next: {runs})"


def preview(prompt: str, limit: int = 120) -> str:
    line = " ".join(prompt.split())
    return line if len(line) <= limit else line[:limit] + "…"


def summarize_job(job: dict, channel_name: str, creator_name: str) -> str:
    """One job in two lines, for `/schedule list` and `list_scheduled_prompts`."""
    when = describe_schedule(job["cron"], job["tz"])
    if job.get("status") == "paused":
        state = "paused" + (f" ({job['paused_reason']})" if job.get("paused_reason") else "")
    elif job.get("next_run"):
        state = "next " + format_run(parse_iso(job["next_run"]), job["tz"])
    else:
        state = "active"
    return (
        f"`{job['job_id']}` — {when} in #{channel_name}, set by {creator_name}; {state}\n"
        f"  “{preview(job['prompt'], 100)}”"
    )
