"""Turns the results channel's history into saved daily results.

Every sync does the same thing: find the days from `first_day` through the
last finished day that aren't marked synced, read the channel's messages for
that span oldest first, and save each day as soon as its messages are in. That
one routine is the daily job, the recovery after downtime, and the backfill:

- The worker runs it every few minutes. Normally one day is pending, just after
  midnight; after an outage, every missed day is, and it catches up.
- A day whose leftover messages the LLM couldn't read is saved with the rule
  results and flagged, and is retried on later syncs.
- /framed backfill moves `first_day` back and forgets the synced marks for a
  range, and the next sync re-reads it.

Discord is kept out of this module: the caller passes a function that yields
ChatMessages for a time span.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import (
    Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Tuple,
)

from bot.domain.framed.interpreter import (
    DayMessage, InterpretError, LLMCall, interpret_day,
)
from bot.domain.framed.parser import (
    ParsedResult, parse_miss_shorthand, parse_share, parse_simple,
)
from bot.domain.framed.puzzle import (
    FRAMED_EPOCH, date_range, day_end, day_start, get_tz,
    latest_complete_day, local_date, puzzle_for_date,
)
from bot.app.utils.logger import get_logger

logger = get_logger()

# Give up retrying the LLM on a day after this many failed syncs.
MAX_LLM_ATTEMPTS = 5
# Longer messages are conversation, not results; don't send them to the LLM.
MAX_LLM_TEXT = 300

# Sources that state a score outright, rather than being read out of chatter.
EXPLICIT = ("share", "number", "word")


def _is_a_remark(parsed: ParsedResult) -> bool:
    if parsed.source == "shorthand":
        return True
    return parsed.source == "llm" and not parsed.corrects_earlier


@dataclass(frozen=True)
class ChatMessage:
    id: int
    author_id: str
    author_name: str
    created_at: datetime
    content: str
    is_bot: bool = False


# (after, before) -> the channel's messages in that span, oldest first
HistoryFetcher = Callable[[datetime, datetime], AsyncIterator[ChatMessage]]
ProgressCallback = Callable[[date, int], Awaitable[None]]


@dataclass
class SyncReport:
    synced_days: List[date] = field(default_factory=list)
    llm_failed_days: List[date] = field(default_factory=list)
    remaining_days: int = 0

    @property
    def results_changed(self) -> bool:
        return bool(self.synced_days)


def pending_days(
    config: Dict[str, Any], synced: Dict[str, Dict[str, Any]], now: datetime
) -> List[date]:
    """Days that need reading, oldest first."""
    tz = get_tz(config.get("timezone"))
    try:
        first = date.fromisoformat(config.get("first_day") or "")
    except ValueError:
        first = latest_complete_day(now, tz)
    first = max(first, FRAMED_EPOCH)
    pending = []
    for day in date_range(first, latest_complete_day(now, tz)):
        meta = synced.get(day.isoformat())
        if meta is None:
            pending.append(day)
        elif meta.get("llm_failed") and int(meta.get("attempts") or 0) < MAX_LLM_ATTEMPTS:
            pending.append(day)
    return pending


async def read_day(
    day: date, messages: List[ChatMessage], tz: Any, llm: Optional[LLMCall]
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str], bool]:
    """(results by user id, display names, whether the LLM failed) for one day."""
    puzzle = puzzle_for_date(day)
    readings: List[Tuple[ChatMessage, Optional[ParsedResult]]] = []
    for msg in messages:
        if msg.is_bot or not msg.content.strip():
            continue
        share = parse_share(msg.content)
        if share is not None:
            if share.puzzle != puzzle:
                # An old puzzle from the archive, or a day late: doesn't count.
                continue
            readings.append((msg, share))
            continue
        simple = parse_simple(msg.content) or parse_miss_shorthand(msg.content)
        if simple is None and len(msg.content) > MAX_LLM_TEXT:
            continue
        readings.append((msg, simple))

    llm_failed = False
    if llm is not None and any(parsed is None for _, parsed in readings):
        day_messages = [
            DayMessage(
                index=i,
                author=msg.author_name,
                time=msg.created_at.astimezone(tz).strftime("%H:%M"),
                text=msg.content,
                parsed_score=parsed.score if parsed else None,
            )
            for i, (msg, parsed) in enumerate(readings)
        ]
        try:
            scores = await interpret_day(day, day_messages, llm)
        except InterpretError as e:
            logger.warning(f"Framed LLM read failed for {day}: {e}")
            llm_failed = True
            scores = {}
        readings = [
            (msg, ParsedResult(score=scores[i][0], source="llm",
                               corrects_earlier=scores[i][1]) if i in scores else parsed)
            for i, (msg, parsed) in enumerate(readings)
        ]

    # Last result per person wins, so a correction replaces the first post.
    results: Dict[str, Dict[str, Any]] = {}
    names: Dict[str, str] = {}
    for msg, parsed in sorted(readings, key=lambda r: r[0].created_at):
        if parsed is None:
            continue
        posted = results.get(msg.author_id)
        if posted is not None and _is_a_remark(parsed) and posted["source"] in EXPLICIT:
            # "F" after posting a score is sympathy for someone else's miss,
            # and "picked wrong on 3" is talk about a round already reported.
            # Only another real score, or an explicit correction, replaces one.
            continue
        results[msg.author_id] = {
            "score": parsed.score,
            "message_id": str(msg.id),
            "posted_at": msg.created_at.astimezone(timezone.utc).isoformat(),
            "source": parsed.source,
        }
        names[msg.author_id] = msg.author_name
    return results, names, llm_failed


async def sync_guild(
    store: Any,
    guild_id: str,
    fetch_history: HistoryFetcher,
    llm: Optional[LLMCall],
    now: Optional[datetime] = None,
    max_days: Optional[int] = None,
    progress: Optional[ProgressCallback] = None,
) -> SyncReport:
    """Read and save every pending day, up to `max_days` of them."""
    now = now or datetime.now(timezone.utc)
    report = SyncReport()
    config = await store.get_config(guild_id)
    if not config:
        return report
    tz = get_tz(config.get("timezone"))
    synced = await store.get_synced(guild_id)
    pending = pending_days(config, synced, now)
    if max_days is not None and len(pending) > max_days:
        report.remaining_days = len(pending) - max_days
        pending = pending[:max_days]
    if not pending:
        return report

    index = 0
    current: Optional[date] = None
    buffer: List[ChatMessage] = []

    async def finish(day: date, messages: List[ChatMessage]) -> None:
        results, names, llm_failed = await read_day(day, messages, tz, llm)
        previous = synced.get(day.isoformat()) or {}
        attempts = int(previous.get("attempts") or 0) + 1 if llm_failed else 0
        await store.save_day(guild_id, day.isoformat(), results, {
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "players": len(results),
            "llm_failed": llm_failed,
            "attempts": attempts,
        })
        await store.set_players(guild_id, names)
        report.synced_days.append(day)
        if llm_failed:
            report.llm_failed_days.append(day)
        if progress is not None:
            await progress(day, len(results))

    async def finish_before(limit: date) -> None:
        nonlocal index
        while index < len(pending) and pending[index] < limit:
            day = pending[index]
            await finish(day, buffer if day == current else [])
            index += 1

    async for msg in fetch_history(day_start(pending[0], tz), day_end(pending[-1], tz)):
        day = local_date(msg.created_at, tz)
        if day != current:
            await finish_before(day)
            current = day
            buffer = []
        buffer.append(msg)
    await finish_before(pending[-1] + timedelta(days=1))

    await store.update_status(guild_id, {
        "last_sync_at": now.isoformat(),
        "last_synced_day": pending[-1].isoformat(),
    })
    return report


async def prepare_backfill(
    store: Any, guild_id: str, start: date, end: date
) -> int:
    """Mark start..end for re-reading, extending tracking back to `start`.

    Returns how many days will be read. The caller then runs sync_guild.
    """
    config = await store.get_config(guild_id)
    if not config:
        return 0
    start = max(start, FRAMED_EPOCH)
    try:
        first = date.fromisoformat(config.get("first_day") or "")
    except ValueError:
        first = start
    if start < first:
        config["first_day"] = start.isoformat()
        await store.save_config(guild_id, config)
    days = [d.isoformat() for d in date_range(start, end)]
    await store.unmark_days(guild_id, days)
    return len(days)
