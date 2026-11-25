"""Asks the utility model whether a message puts something on the calendar.

One call does both jobs: deciding whether the message names a real, upcoming
plan and pulling out its title and day. Splitting those would double the
cost of every positive for no gain, since the extraction prompt has to make
the same judgement anyway.

The model is told when the message was sent, in the server's zone, so
"Friday" and "the 28th" resolve against the right week. It returns ISO dates
and 24-hour times; everything it returns is re-checked in code (parseable,
not in the past, not absurdly far out, confident enough), because a model
that is asked for a date will happily write one that does not exist.

One correction is made rather than rejected: a month and day with no year
("March 13 is CRSSD") that the model has placed earlier this year is moved to
next year, since the message plainly meant the next one coming. Without
this, every plan for early next year mentioned in the autumn was dropped as
"past" with the model at full confidence.

Shares `extract_json`, `ExtractError` and the production `openai_llm` with the
channel-scan extractor: same model, same wire format, same failure handling.
"""

import datetime as dt
import json
import re
from typing import Awaitable, Callable, List, Optional

from bot.domain.calendar.gate import MONTHS
from bot.domain.calendar.models import CaptureContext, ChatLine, ExtractedEvent, zone
from bot.domain.scan.extractor import ExtractError, extract_json, openai_llm

LLMCall = Callable[[str, str], Awaitable[str]]

MIN_CONFIDENCE = 0.6
MAX_DAYS_AHEAD = 366 * 2
MAX_TITLE_CHARS = 80
MAX_MESSAGE_CHARS = 700
MAX_CONTEXT_CHARS = 200

_WHITESPACE = re.compile(r"\s+")
_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
# A day named by month ("march 13", "13th of March", "3/13") with no year. The
# model tends to write this year's date for these even when that day has
# passed; such a date is read as the next time the day comes around. A
# weekday or "the 28th" is left alone: the model already resolves those
# against the sent date.
_MONTH_DAY = re.compile(
    r"(?<![a-z])(?:" + MONTHS + r")(?![a-z])\.?\s+\d{1,2}\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:" + MONTHS + r")(?![a-z])"
    r"|\b\d{1,2}[/.-]\d{1,2}\b(?![/.-]\d)",
    re.IGNORECASE,
)
_STATED_YEAR = re.compile(r"\b(?:19|20)\d{2}\b|\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2}\b")

SYSTEM_PROMPT = (
    "You maintain a shared calendar for a Discord server. You read one chat "
    "message and decide whether it puts a concrete, upcoming plan or event on "
    "the calendar. Return only JSON."
)


def _render(line: ChatLine, tz_name: str, limit: int) -> str:
    content = _WHITESPACE.sub(" ", line.content or "").strip()
    if len(content) > limit:
        content = content[:limit] + "…"
    local = line.sent_at.astimezone(zone(tz_name))
    return "[%s] %s: %s" % (local.strftime("%a %Y-%m-%d %H:%M"), line.author_name, content)


def build_prompt(context: CaptureContext) -> str:
    tz_name = context.timezone
    sent_local = context.message.sent_at.astimezone(zone(tz_name))
    previous = "\n".join(_render(p, tz_name, MAX_CONTEXT_CHARS) for p in context.previous)
    return (
        "Server timezone: %s\n"
        "The message was sent on %s (%s) at %s local time.\n\n"
        "Earlier messages, for context only (do not take events from these):\n%s\n\n"
        "The message to judge:\n%s\n\n"
        "Add an entry only when the message itself proposes, announces, or confirms "
        "something happening on a specific future day that the group would want on "
        "a shared calendar: a game night, a trip, a release, a birthday party, a "
        "deadline, a stream, a match. Resolve relative words (\"Friday\", \"next "
        "week\", \"the 28th\") against the sent date above; a bare weekday means the "
        "next one coming, including today if it has not passed. A month and day with "
        "no year (\"March 13\", \"Dec 5\") also mean the next one coming: if that day "
        "has already passed this year, write next year's date.\n"
        "Do NOT add: things in the past; vague or hypothetical timing (\"sometime in "
        "March\", \"we should do this one day\"); facts and trivia that merely contain "
        "a date; things minutes or a few hours away that nobody needs reminding of "
        "(\"brb in 10\", \"hopping on at 8\" said at 7:30); questions about availability "
        "with no plan behind them; recurring schedules without a first date.\n"
        "- \"title\": a few words naming the plan, as the group would say it. No dates in it.\n"
        "- \"date\": YYYY-MM-DD of the start.\n"
        "- \"time\": HH:MM in 24-hour local time, or null when none is given.\n"
        "- \"end_date\": YYYY-MM-DD for the last day of a multi-day plan, else null.\n"
        "- \"confidence\": 0 to 1, how sure you are this is a real plan on that day.\n\n"
        "Reply as JSON: {\"events\": [{\"title\": \"...\", \"date\": \"YYYY-MM-DD\", "
        "\"time\": \"HH:MM\" or null, \"end_date\": \"YYYY-MM-DD\" or null, "
        "\"confidence\": 0.0}]}. Use an empty list when there is nothing to add."
        % (
            tz_name,
            sent_local.strftime("%A"),
            sent_local.strftime("%Y-%m-%d"),
            sent_local.strftime("%H:%M"),
            previous or "(none)",
            _render(context.message, tz_name, MAX_MESSAGE_CHARS),
        )
    )


def _clean_title(raw: object) -> str:
    title = _WHITESPACE.sub(" ", str(raw or "")).strip().strip("\"'`*_ ")
    return title[:MAX_TITLE_CHARS].rstrip()


def _parse_date(raw: object) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(raw or "").strip()[:10])
    except ValueError:
        return None


def _parse_time(raw: object) -> Optional[str]:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() in ("null", "none", "all day", "all-day"):
        return None
    match = _TIME.match(text[:5]) if len(text) >= 4 else None
    if not match:
        return None
    return "%02d:%02d" % (int(match.group(1)), int(match.group(2)))


def _next_year(day: dt.date) -> dt.date:
    try:
        return day.replace(year=day.year + 1)
    except ValueError:  # Feb 29
        return day.replace(year=day.year + 1, day=28)


def parse_reply(reply: str, context: CaptureContext) -> List[ExtractedEvent]:
    """The usable events in the model's reply.

    Raises ExtractError when the reply is unreadable as a whole; drops
    individual entries that fail validation rather than failing the message.
    """
    try:
        data = json.loads(extract_json(reply))
    except json.JSONDecodeError as e:
        raise ExtractError("Bad JSON in model reply: %s" % e)
    if not isinstance(data, dict):
        raise ExtractError("Model reply was not a JSON object")
    raw_events = data.get("events")
    if raw_events is None:
        raise ExtractError("Model reply has no events list")
    if not isinstance(raw_events, list):
        raise ExtractError("Model reply's events was not a list")

    today = context.message.sent_at.astimezone(zone(context.timezone)).date()
    horizon = today + dt.timedelta(days=MAX_DAYS_AHEAD)
    text = context.message.content or ""
    roll_forward = bool(_MONTH_DAY.search(text)) and not _STATED_YEAR.search(text)
    events: List[ExtractedEvent] = []
    seen = set()
    for entry in raw_events:
        if not isinstance(entry, dict):
            continue
        title = _clean_title(entry.get("title"))
        date = _parse_date(entry.get("date"))
        if not title or date is None:
            continue
        end = _parse_date(entry.get("end_date")) if entry.get("end_date") else None
        if roll_forward and date < today and today - date <= dt.timedelta(days=366):
            # "march 13" said in November: the model wrote this year's March.
            date = _next_year(date)
            end = _next_year(end) if end is not None else None
        if date < today or date > horizon:
            continue
        try:
            confidence = float(entry.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < MIN_CONFIDENCE:
            continue
        if end is not None and (end <= date or end > horizon):
            end = None
        key = (title.lower(), date.isoformat())
        if key in seen:
            continue
        seen.add(key)
        events.append(ExtractedEvent(
            title=title,
            date=date.isoformat(),
            time=_parse_time(entry.get("time")),
            end_date=end.isoformat() if end else None,
            confidence=round(confidence, 2),
        ))
    return events


async def extract_events(
    context: CaptureContext, llm: Optional[LLMCall] = None
) -> List[ExtractedEvent]:
    """Events the message puts on the calendar. Raises ExtractError."""
    call = llm or openai_llm
    prompt = build_prompt(context)
    try:
        reply = await call(SYSTEM_PROMPT, prompt)
    except ExtractError:
        raise
    except Exception as e:
        raise ExtractError("Model call failed: %s" % e)
    return parse_reply(reply, context)
