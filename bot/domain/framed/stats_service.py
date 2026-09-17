"""Loads saved Framed results and renders them as text.

The slash commands, the daily recap, and the agent tool all read through here,
so the numbers and their wording are the same everywhere.
"""

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from bot.domain.framed.puzzle import (
    FAIL, MAX_GUESSES, date_for_puzzle, format_score, get_tz, latest_complete_day,
    points_for, puzzle_for_date,
)
from bot.domain.framed.stats import (
    HeadToHead, LeaderboardRow, PlayerStats, Scores, daily_ranking,
    filter_period, leaderboard,
)
from bot.domain.framed.sync_service import MAX_LLM_ATTEMPTS, pending_days

# The worker ticks every 10 minutes; well past that means it isn't running.
WORKER_STALE_AFTER = timedelta(hours=1)

PERIODS = ("week", "month", "year", "all")


@dataclass
class FramedData:
    config: Dict[str, Any]
    scores: Scores
    names: Dict[str, str]
    status: Dict[str, Any]
    latest_day: date                      # the last finished day
    as_of: date                           # the last finished day that's been read
    pending: List[date] = field(default_factory=list)
    unreadable: List[date] = field(default_factory=list)

    def name(self, user_id: str) -> str:
        return self.names.get(user_id) or "Unknown player"


async def load(store: Any, guild_id: str, now: Optional[datetime] = None) -> Optional[FramedData]:
    now = now or datetime.now(timezone.utc)
    config = await store.get_config(guild_id)
    if not config:
        return None
    tz = get_tz(config.get("timezone"))
    synced = await store.get_synced(guild_id)
    raw = await store.get_days(guild_id, synced.keys())

    scores: Scores = {}
    for day_str, results in raw.items():
        try:
            day = date.fromisoformat(day_str)
        except ValueError:
            continue
        scores[day] = {uid: int(r.get("score", FAIL)) for uid, r in results.items()}

    for key, override in (await store.get_overrides(guild_id)).items():
        day_str, _, uid = key.partition(":")
        try:
            day = date.fromisoformat(day_str)
        except ValueError:
            continue
        if override.get("score") is None:
            scores.get(day, {}).pop(uid, None)
        else:
            scores.setdefault(day, {})[uid] = int(override["score"])

    unreadable = sorted(
        date.fromisoformat(d) for d, meta in synced.items()
        if meta.get("llm_failed") and int(meta.get("attempts") or 0) >= MAX_LLM_ATTEMPTS
    )
    latest_day = latest_complete_day(now, tz)
    pending = pending_days(config, synced, now)
    # Streaks run back from the newest day that's been read, so an unread
    # yesterday doesn't zero everyone's current streak.
    as_of = latest_day
    while as_of in pending:
        as_of -= timedelta(days=1)
    return FramedData(
        config=config,
        scores={d: s for d, s in scores.items() if s},
        names=await store.get_players(guild_id),
        status=await store.get_status(guild_id),
        latest_day=latest_day,
        as_of=as_of,
        pending=pending,
        unreadable=unreadable,
    )


def resolve_period(
    period: str, latest_day: date, year: Optional[int] = None
) -> Tuple[Optional[date], Optional[date], str]:
    """(start, end, label). Month and year are the ones `latest_day` falls in."""
    if year is not None:
        return date(year, 1, 1), date(year, 12, 31), str(year)
    if period == "week":
        return latest_day - timedelta(days=6), latest_day, "Last 7 days"
    if period == "month":
        start = latest_day.replace(day=1)
        return start, latest_day, "%s %d" % (calendar.month_name[start.month], start.year)
    if period == "year":
        return date(latest_day.year, 1, 1), latest_day, str(latest_day.year)
    return None, None, "All time"


def parse_day(text: Optional[str], latest_day: date) -> Optional[date]:
    """'1650', '#1650', '2026-09-16', 'yesterday', or blank (the latest day)."""
    cleaned = (text or "").strip().lstrip("#").lower()
    if not cleaned or cleaned == "yesterday":
        return latest_day
    if cleaned.isdigit():
        return date_for_puzzle(int(cleaned))
    try:
        return date.fromisoformat(cleaned)
    except ValueError:
        return None


def find_players(names: Dict[str, str], query: str) -> List[str]:
    """User ids whose name matches: exact (case-insensitive) first, else substring."""
    q = (query or "").strip().lower().lstrip("@")
    if not q:
        return []
    exact = [uid for uid, n in names.items() if n.lower() == q]
    if exact:
        return exact
    return [uid for uid, n in names.items() if q in n.lower()]


# --- Text ---

def _day_label(day: date) -> str:
    return "Framed #%d · %s %d, %d" % (
        puzzle_for_date(day), day.strftime("%a %b"), day.day, day.year)


def format_day(data: FramedData, day: date) -> Tuple[str, str]:
    """(title, body) for one day's ranking."""
    title = _day_label(day)
    day_scores = data.scores.get(day, {})
    if not day_scores:
        if day > data.latest_day:
            return title, "That day isn't over yet. Results are read after midnight."
        if day in data.pending:
            return title, "That day hasn't been read yet."
        return title, "Nobody posted a result."
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = []
    for entry in daily_ranking(day_scores):
        badge = medals.get(entry.rank, "") if entry.score != FAIL else "💀"
        lines.append("%s **%d.** %s — %s/6 · %d pt%s" % (
            badge, entry.rank, data.name(entry.user_id),
            format_score(entry.score), entry.points,
            "" if entry.points == 1 else "s",
        ))
    return title, "\n".join(line.strip() for line in lines)


def format_leaderboard(
    rows: List[LeaderboardRow], data: FramedData, limit: int = 15
) -> str:
    if not rows:
        return "No results in this period."
    lines = []
    for row in rows[:limit]:
        lines.append(
            "**%d.** %s — **%d pts** · %d played · %.1f/game · %d win%s · %d%% solved" % (
                row.rank, data.name(row.user_id), row.points, row.played,
                row.points_per_game, row.wins, "" if row.wins == 1 else "s",
                round(100 * row.solved / row.played) if row.played else 0,
            )
        )
    if len(rows) > limit:
        lines.append("…and %d more" % (len(rows) - limit))
    return "\n".join(lines)


def leaderboard_for(
    data: FramedData, period: str, year: Optional[int] = None
) -> Tuple[str, List[LeaderboardRow]]:
    start, end, label = resolve_period(period, data.latest_day, year)
    return label, leaderboard(filter_period(data.scores, start, end))


def format_player(data: FramedData, stats: PlayerStats) -> str:
    if not stats.played:
        return "No results yet."
    lifetime = {r.user_id: r for r in leaderboard(data.scores)}
    _, year_rows = leaderboard_for(data, "year")
    year_rank = next((r.rank for r in year_rows if r.user_id == stats.user_id), None)
    avg = stats.average_guesses
    lines = [
        "**Played:** %d (first %s, last %s)" % (
            stats.played, stats.first_played, stats.last_played),
        "**Points:** %d · %.2f per game" % (stats.points, stats.points_per_game),
        "**Average guess (solved):** %s · **Solved:** %d%% · **Perfect 1s:** %d" % (
            "%.2f" % avg if avg is not None else "—",
            round(100 * stats.solve_rate), stats.perfect),
        "**Daily wins:** %d" % stats.wins,
        "**Rank:** #%d all-time · %s this year" % (
            lifetime[stats.user_id].rank,
            "#%d" % year_rank if year_rank else "unranked"),
        "**Solve streak:** %d current · %d longest" % (
            stats.current_solve_streak, stats.longest_solve_streak),
        "**Play streak:** %d current · %d longest" % (
            stats.current_play_streak, stats.longest_play_streak),
        "",
        "```",
        distribution_chart(stats),
        "```",
    ]
    return "\n".join(lines)


def distribution_chart(stats: PlayerStats, width: int = 20) -> str:
    biggest = max(stats.distribution.values()) or 1
    rows = []
    for score in list(range(1, MAX_GUESSES + 1)) + [FAIL]:
        count = stats.distribution.get(score, 0)
        bar = "█" * max(1 if count else 0, round(width * count / biggest))
        rows.append("%s │%s %d" % (format_score(score), bar, count))
    return "\n".join(rows)


def format_head_to_head(data: FramedData, a: str, b: str, h2h: HeadToHead) -> str:
    if not h2h.days:
        return "%s and %s haven't played on the same day yet." % (data.name(a), data.name(b))
    return (
        "Over %d day%s both played:\n**%s** did better %d time%s\n**%s** did better %d time%s\n"
        "Tied %d time%s" % (
            h2h.days, "" if h2h.days == 1 else "s",
            data.name(a), h2h.a_better, "" if h2h.a_better == 1 else "s",
            data.name(b), h2h.b_better, "" if h2h.b_better == 1 else "s",
            h2h.tied, "" if h2h.tied == 1 else "s",
        )
    )


def sync_warning(data: FramedData, now: Optional[datetime] = None) -> Optional[str]:
    """A one-line heads-up when the numbers may be incomplete, else None."""
    now = now or datetime.now(timezone.utc)
    notes = []
    if data.pending:
        notes.append(
            "%d day%s not read yet (oldest %s)" % (
                len(data.pending), "" if len(data.pending) == 1 else "s",
                data.pending[0].isoformat())
        )
    worker_run = data.status.get("last_worker_run_at")
    try:
        stale = (
            worker_run is None
            or now - datetime.fromisoformat(worker_run) > WORKER_STALE_AFTER
        )
    except ValueError:
        stale = True
    if data.pending and stale:
        notes.append("the sync worker isn't running — try `/framed sync`")
    if data.unreadable:
        notes.append(
            "%d day%s had posts the bot couldn't read — see `/framed status`" % (
                len(data.unreadable), "" if len(data.unreadable) == 1 else "s")
        )
    return "⚠️ " + "; ".join(notes) if notes else None


def points_label(score: int) -> str:
    return "%s/6 (%d pts)" % (format_score(score), points_for(score))
