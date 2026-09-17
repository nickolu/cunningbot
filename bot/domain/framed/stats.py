"""Framed statistics, computed from saved results on every request.

Input everywhere is `scores`: {date: {user_id: score}} with score 1-6, or 0 for
a miss. A player who didn't post on a day has no entry for it. Only days that
have been synced appear, so a day with no entry at all is one nobody played or
one that hasn't been read yet; `SyncState` says which.

Definitions:
- Points: 1 → 6 ... 6 → 1, miss → 0. Rankings over any period sort by total
  points, then points per game, then wins.
- Daily ranking: by score, misses last; tied scores share a rank. The day's
  winners are everyone tied for the best score, and a day where everyone
  missed has no winner.
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


@dataclass
class PlayerStats:
    user_id: str
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
    def perfect(self) -> int:
        return self.distribution.get(1, 0)

    @property
    def solve_rate(self) -> float:
        return self.solved / self.played if self.played else 0.0

    @property
    def points_per_game(self) -> float:
        return self.points / self.played if self.played else 0.0

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

    @property
    def points_per_game(self) -> float:
        return self.points / self.played if self.played else 0.0


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


def leaderboard(scores: Scores) -> List[LeaderboardRow]:
    rows: Dict[str, LeaderboardRow] = {}
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

    def key(r: LeaderboardRow) -> tuple:
        return (-r.points, -round(r.points_per_game, 6), -r.wins)

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


def player_stats(scores: Scores, user_id: str, as_of: Optional[date]) -> PlayerStats:
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
