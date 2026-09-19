"""Framed statistics, computed from saved results on every request.

Input everywhere is `scores`: {date: {user_id: score}} with score 1-6, or 0 for
a miss. A player who didn't post on a day has no entry for it. Only days that
have been synced appear, so a day with no entry at all is one nobody played or
one that hasn't been read yet; `SyncState` says which.

Not posting counts as a miss. People here often stay quiet on a day they
didn't get it, so a day with no post is scored the same as one: 0 points, and
it counts against the solve rate. Which days count is the `scope`:

- Lifetime: every day from that player's own first result through `as_of`, so
  someone who joined in 2024 isn't judged on 2022.
- A week, month or year: every day of that period (bounded by `as_of` and by
  the day the group started playing), so joining midway through a year doesn't
  wipe out the months before.

Definitions:
- Points: 1 → 6 ... 6 → 1, miss → 0, no post → 0. Rankings over any period sort
  by total points, then points per day, then wins.
- Daily ranking: by score, misses last; tied scores share a rank. The day's
  winners are everyone tied for the best score, and a day where everyone
  missed has no winner.
- Solve rate: days solved ÷ days in scope, so missed and unposted days both
  lower it. `played` stays the count of days actually posted.
- Solve streak: consecutive days solved. A miss or a day not played ends it.
- Play streak: consecutive days posted, misses included.
- Current streaks run back from `as_of`, the last day that has been synced.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

from bot.domain.framed.puzzle import FAIL, MAX_GUESSES, points_for

Scores = Dict[date, Dict[str, int]]


@dataclass
class DailyEntry:
    rank: int
    user_id: str
    score: int
    points: int
    winner: bool


# (start, end) of the days a player is judged on. A None start means "from
# that player's own first result", which is what lifetime figures use.
Scope = Tuple[Optional[date], date]


def scope_length(scope: Optional[Scope], first_played: Optional[date]) -> int:
    """How many days a player is judged on, given their first result."""
    if scope is None:
        return 0
    start, end = scope
    if start is None:
        start = first_played
    if start is None or end < start:
        return 0
    return (end - start).days + 1


@dataclass
class PlayerStats:
    user_id: str
    days: int = 0        # days in scope: the denominator, unposted days included
    played: int = 0
    solved: int = 0
    points: int = 0
    wins: int = 0
    distribution: Dict[int, int] = field(
        default_factory=lambda: {s: 0 for s in range(0, MAX_GUESSES + 1)}
    )
    current_solve_streak: int = 0
    longest_solve_streak: int = 0
    current_play_streak: int = 0
    longest_play_streak: int = 0
    first_played: Optional[date] = None
    last_played: Optional[date] = None

    @property
    def missed(self) -> int:
        return self.played - self.solved

    @property
    def absent(self) -> int:
        """Days in scope with no post at all, which count as misses."""
        return max(0, self.days - self.played)

    @property
    def perfect(self) -> int:
        return self.distribution.get(1, 0)

    @property
    def solve_rate(self) -> float:
        """Share of days in scope that were solved. Not posting counts against."""
        denominator = self.days or self.played
        return self.solved / denominator if denominator else 0.0

    @property
    def play_rate(self) -> float:
        return self.played / self.days if self.days else 0.0

    @property
    def points_per_day(self) -> float:
        denominator = self.days or self.played
        return self.points / denominator if denominator else 0.0

    @property
    def average_guesses(self) -> Optional[float]:
        """Average guess number over solved games only."""
        if not self.solved:
            return None
        total = sum(s * n for s, n in self.distribution.items() if s != FAIL)
        return total / self.solved


@dataclass
class LeaderboardRow:
    rank: int
    user_id: str
    points: int
    played: int
    solved: int
    wins: int
    days: int = 0

    @property
    def points_per_day(self) -> float:
        denominator = self.days or self.played
        return self.points / denominator if denominator else 0.0

    @property
    def solve_rate(self) -> float:
        denominator = self.days or self.played
        return self.solved / denominator if denominator else 0.0


@dataclass
class HeadToHead:
    days: int = 0
    a_better: int = 0
    b_better: int = 0
    tied: int = 0


def _competition_ranks(values: List[Tuple[str, tuple]]) -> List[Tuple[int, str]]:
    """values sorted best-first as (user_id, key); equal keys share a rank."""
    ranked: List[Tuple[int, str]] = []
    previous = None
    rank = 0
    for position, (uid, key) in enumerate(values, start=1):
        if key != previous:
            rank = position
            previous = key
        ranked.append((rank, uid))
    return ranked


def _sort_score(score: int) -> int:
    return score if 1 <= score <= MAX_GUESSES else MAX_GUESSES + 1


def daily_ranking(day_scores: Dict[str, int]) -> List[DailyEntry]:
    ordered = sorted(day_scores.items(), key=lambda kv: (_sort_score(kv[1]), kv[0]))
    best = min((s for s in day_scores.values() if s != FAIL), default=None)
    ranks = _competition_ranks([(uid, (_sort_score(s),)) for uid, s in ordered])
    return [
        DailyEntry(
            rank=rank,
            user_id=uid,
            score=day_scores[uid],
            points=points_for(day_scores[uid]),
            winner=best is not None and day_scores[uid] == best,
        )
        for rank, uid in ranks
    ]


def winners(day_scores: Dict[str, int]) -> List[str]:
    return [e.user_id for e in daily_ranking(day_scores) if e.winner]


def filter_period(scores: Scores, start: Optional[date], end: Optional[date]) -> Scores:
    return {
        d: s for d, s in scores.items()
        if (start is None or d >= start) and (end is None or d <= end)
    }


def leaderboard(scores: Scores, scope: Optional[Scope] = None) -> List[LeaderboardRow]:
    rows: Dict[str, LeaderboardRow] = {}
    first_played: Dict[str, date] = {}
    for day, day_scores in sorted(scores.items()):
        for uid in day_scores:
            first_played.setdefault(uid, day)
    for day_scores in scores.values():
        day_winners = set(winners(day_scores))
        for uid, score in day_scores.items():
            row = rows.setdefault(uid, LeaderboardRow(0, uid, 0, 0, 0, 0))
            row.played += 1
            row.points += points_for(score)
            if score != FAIL:
                row.solved += 1
            if uid in day_winners:
                row.wins += 1

    for uid, row in rows.items():
        row.days = scope_length(scope, first_played.get(uid))

    def key(r: LeaderboardRow) -> tuple:
        return (-r.points, -round(r.points_per_day, 6), -r.wins)

    ordered = sorted(rows.values(), key=lambda r: key(r) + (r.user_id,))
    for rank, uid in _competition_ranks([(r.user_id, key(r)) for r in ordered]):
        rows[uid].rank = rank
    return ordered


def _streaks(days: Iterable[date], as_of: Optional[date]) -> Tuple[int, int]:
    """(current, longest) runs of consecutive dates."""
    ordered = sorted(set(days))
    longest = 0
    run = 0
    previous: Optional[date] = None
    for day in ordered:
        run = run + 1 if previous is not None and day - previous == timedelta(days=1) else 1
        longest = max(longest, run)
        previous = day
    current = 0
    if as_of is not None and ordered and ordered[-1] == as_of:
        current = run
    return current, longest


def player_stats(
    scores: Scores,
    user_id: str,
    as_of: Optional[date],
    scope: Optional[Scope] = None,
) -> PlayerStats:
    stats = PlayerStats(user_id=user_id)
    played_days: List[date] = []
    solved_days: List[date] = []
    for day in sorted(scores):
        day_scores = scores[day]
        if user_id not in day_scores:
            continue
        score = day_scores[user_id]
        stats.played += 1
        stats.points += points_for(score)
        stats.distribution[score if 1 <= score <= MAX_GUESSES else FAIL] += 1
        played_days.append(day)
        if score != FAIL:
            stats.solved += 1
            solved_days.append(day)
        if user_id in winners(day_scores):
            stats.wins += 1
    if played_days:
        stats.first_played = played_days[0]
        stats.last_played = played_days[-1]
    stats.days = scope_length(scope, stats.first_played)
    stats.current_play_streak, stats.longest_play_streak = _streaks(played_days, as_of)
    stats.current_solve_streak, stats.longest_solve_streak = _streaks(solved_days, as_of)
    return stats


def head_to_head(scores: Scores, a: str, b: str) -> HeadToHead:
    result = HeadToHead()
    for day_scores in scores.values():
        if a not in day_scores or b not in day_scores:
            continue
        result.days += 1
        sa, sb = _sort_score(day_scores[a]), _sort_score(day_scores[b])
        if sa < sb:
            result.a_better += 1
        elif sb < sa:
            result.b_better += 1
        else:
            result.tied += 1
    return result
