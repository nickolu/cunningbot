"""Plain data for the calendar feature. No Discord, no Redis."""

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, Optional, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "America/Los_Angeles"

# Friendly names people type, mapped to IANA zones.
TIMEZONE_ALIASES: Dict[str, str] = {
    "pacific": "America/Los_Angeles",
    "pt": "America/Los_Angeles",
    "pst": "America/Los_Angeles",
    "pdt": "America/Los_Angeles",
    "mountain": "America/Denver",
    "mt": "America/Denver",
    "central": "America/Chicago",
    "ct": "America/Chicago",
    "eastern": "America/New_York",
    "et": "America/New_York",
    "est": "America/New_York",
    "edt": "America/New_York",
    "utc": "UTC",
    "gmt": "UTC",
    "london": "Europe/London",
    "uk": "Europe/London",
}


def resolve_timezone_name(name: Optional[str]) -> Optional[str]:
    """An IANA zone name for what someone typed, or None if it is not one."""
    raw = (name or "").strip()
    if not raw:
        return DEFAULT_TIMEZONE
    candidate = TIMEZONE_ALIASES.get(raw.lower(), raw)
    try:
        ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return None
    return candidate


def zone(name: Optional[str]) -> ZoneInfo:
    """A ZoneInfo for a stored name, falling back to the default, then UTC."""
    for candidate in (name, DEFAULT_TIMEZONE, "UTC"):
        try:
            return ZoneInfo(candidate or "")
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            continue
    return ZoneInfo("UTC")  # pragma: no cover - UTC always exists


@dataclass(frozen=True)
class CalendarEvent:
    """One entry on a server's calendar.

    ``date`` is the local calendar day; ``time`` is a local "HH:MM" or None for
    an all-day entry. ``end_date`` covers multi-day entries. Times are local to
    ``timezone`` so a stored event reads the same whatever zone the server is
    later switched to.
    """

    id: str
    title: str
    date: str                       # YYYY-MM-DD
    time: Optional[str] = None      # HH:MM, 24h, or None
    end_date: Optional[str] = None  # YYYY-MM-DD, inclusive, or None
    timezone: str = DEFAULT_TIMEZONE
    channel_id: Optional[str] = None
    message_id: Optional[str] = None
    author_id: Optional[str] = None
    author_name: Optional[str] = None
    jump_url: Optional[str] = None
    created_at: Optional[str] = None  # ISO, UTC
    source: str = "capture"           # "capture" or "manual"
    confidence: Optional[float] = None

    def to_record(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "date": self.date,
            "time": self.time,
            "end_date": self.end_date,
            "timezone": self.timezone,
            "channel_id": self.channel_id,
            "message_id": self.message_id,
            "author_id": self.author_id,
            "author_name": self.author_name,
            "jump_url": self.jump_url,
            "created_at": self.created_at,
            "source": self.source,
            "confidence": self.confidence,
        }

    @classmethod
    def from_record(cls, record: Dict[str, object]) -> "CalendarEvent":
        return cls(
            id=str(record.get("id") or ""),
            title=str(record.get("title") or ""),
            date=str(record.get("date") or ""),
            time=_opt_str(record.get("time")),
            end_date=_opt_str(record.get("end_date")),
            timezone=str(record.get("timezone") or DEFAULT_TIMEZONE),
            channel_id=_opt_str(record.get("channel_id")),
            message_id=_opt_str(record.get("message_id")),
            author_id=_opt_str(record.get("author_id")),
            author_name=_opt_str(record.get("author_name")),
            jump_url=_opt_str(record.get("jump_url")),
            created_at=_opt_str(record.get("created_at")),
            source=str(record.get("source") or "capture"),
            confidence=_opt_float(record.get("confidence")),
        )

    def start(self) -> dt.datetime:
        """The event's start as an aware datetime (midnight local for all-day)."""
        day = dt.date.fromisoformat(self.date)
        if self.time:
            hour, minute = (int(p) for p in self.time.split(":")[:2])
        else:
            hour, minute = 0, 0
        return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone(self.timezone))

    def start_epoch(self) -> float:
        return self.start().timestamp()

    def last_day(self) -> dt.date:
        if self.end_date:
            try:
                end = dt.date.fromisoformat(self.end_date)
                if end.isoformat() >= self.date:
                    return end
            except ValueError:
                pass
        return dt.date.fromisoformat(self.date)


def _opt_str(value: object) -> Optional[str]:
    if value is None or value == "":
        return None
    return str(value)


def _opt_float(value: object) -> Optional[float]:
    try:
        return float(value) if value is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ChatLine:
    """One message as the extractor sees it."""

    author_name: str
    content: str
    sent_at: dt.datetime  # aware


@dataclass(frozen=True)
class CaptureContext:
    """What the extractor is given about the message being looked at."""

    message: ChatLine
    previous: Sequence[ChatLine] = field(default_factory=tuple)
    timezone: str = DEFAULT_TIMEZONE


@dataclass(frozen=True)
class ExtractedEvent:
    """What the model proposed, before it becomes a CalendarEvent."""

    title: str
    date: str
    time: Optional[str]
    end_date: Optional[str]
    confidence: float
