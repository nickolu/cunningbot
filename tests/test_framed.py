"""Tests for Framed result tracking: parsing, stats, sync, and catch-up."""

import copy
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest

from bot.domain.framed import stats_service
from bot.domain.framed.interpreter import InterpretError, parse_reply
from bot.domain.framed.parser import parse_message, parse_share, parse_simple
from bot.domain.framed.puzzle import (
    FAIL, date_for_puzzle, get_tz, latest_complete_day, points_for, puzzle_for_date,
)
from bot.domain.framed.stats import (
    daily_ranking, head_to_head, leaderboard, player_stats, winners,
)
from bot.domain.framed.sync_service import (
    MAX_LLM_ATTEMPTS, ChatMessage, pending_days, prepare_backfill, sync_guild,
)

PT = get_tz("America/Los_Angeles")
GUILD = "123"


def share(puzzle: int, squares: str) -> str:
    return (
        f"Framed #{puzzle}\n🎥 {squares}\n\nhttps://framed.wtf/\n"
        "Framed - The daily movie guessing game\nGuess the movie from 6 frames."
    )


# --- Puzzle numbers ---

def test_puzzle_numbers_match_framed_wtf():
    assert puzzle_for_date(date(2022, 3, 12)) == 1
    assert puzzle_for_date(date(2026, 9, 17)) == 1651
    assert date_for_puzzle(1650) == date(2026, 9, 16)


def test_points():
    assert [points_for(s) for s in (1, 2, 3, 4, 5, 6, FAIL)] == [6, 5, 4, 3, 2, 1, 0]


def test_latest_complete_day_uses_pacific_midnight():
    # 2026-09-17 06:30 UTC is 23:30 on the 16th in Pacific time.
    assert latest_complete_day(datetime(2026, 9, 17, 6, 30, tzinfo=timezone.utc), PT) == date(2026, 9, 15)
    # 08:00 UTC is 01:00 Pacific on the 17th, so the 16th is over.
    assert latest_complete_day(datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc), PT) == date(2026, 9, 16)


# --- Parsing ---

@pytest.mark.parametrize("squares, score", [
    ("🟥 🟩 ⬛ ⬛ ⬛ ⬛", 2),
    ("🟩 ⬛ ⬛ ⬛ ⬛ ⬛", 1),
    ("🟥 🟥 🟥 🟥 🟥 🟩", 6),
    ("🟥 🟥 🟥 🟥 🟥 🟥", FAIL),
])
def test_parse_share(squares, score):
    result = parse_share(share(1650, squares))
    assert result.score == score
    assert result.puzzle == 1650
    assert result.source == "share"


def test_share_with_variation_selectors_and_text_before():
    text = "ugh\nFramed #1650\n🎥 🟥 🟥 🟩 ⬛️ ⬛️ ⬛️"
    assert parse_share(text).score == 3


def test_side_games_are_not_the_daily_game():
    text = "Framed - One Frame Challenge #500\n🎥 🟩 ⬛ ⬛ ⬛ ⬛ ⬛"
    assert parse_share(text) is None


@pytest.mark.parametrize("text, score", [
    ("3", 3), (" 3/6 ", 3), ("5!", 5), ("X", FAIL), ("x/6", FAIL), ("0", FAIL),
    ("7", FAIL), ("three", 3), ("Nada", FAIL), ("six.", 6),
])
def test_parse_simple(text, score):
    assert parse_simple(text).score == score


@pytest.mark.parametrize("text", ["8", "12", "got it in 3", "lol", "", "nada this time, brutal"])
def test_parse_simple_leaves_the_rest(text):
    assert parse_message(text) is None


def test_parse_reply_keeps_only_candidates_and_valid_scores():
    reply = 'sure {"results": [{"index": 1, "score": 4}, {"index": 2, "score": "X"}, ' \
            '{"index": 0, "score": 2}, {"index": 3, "score": 9}]}'
    assert parse_reply(reply, [1, 2, 3]) == {1: 4, 2: FAIL}


def test_parse_reply_raises_on_garbage():
    with pytest.raises(InterpretError):
        parse_reply("There was an error: rate limited", [0])


# --- Stats ---

D1, D2, D3, D4 = date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4)


def test_daily_ranking_ties_and_misses():
    ranking = daily_ranking({"a": 3, "b": 2, "c": 3, "d": FAIL})
    assert [(e.rank, e.user_id, e.points, e.winner) for e in ranking] == [
        (1, "b", 5, True), (2, "a", 4, False), (2, "c", 4, False), (4, "d", 0, False),
    ]
    assert sorted(winners({"a": 2, "b": 2, "c": 5})) == ["a", "b"]
    assert winners({"a": FAIL, "b": FAIL}) == []


def test_leaderboard_ranks_by_points():
    scores = {D1: {"a": 1, "b": 2}, D2: {"a": FAIL, "b": 3}, D3: {"b": 6, "c": 1}}
    rows = leaderboard(scores)
    assert [(r.rank, r.user_id, r.points, r.played, r.wins) for r in rows] == [
        # a and c tie on points; c's 6 per game beats a's 3.
        (1, "b", 10, 3, 1), (2, "c", 6, 1, 1), (3, "a", 6, 2, 1),
    ]


def test_streaks():
    scores = {
        D1: {"a": 2}, D2: {"a": FAIL}, D3: {"a": 4},
        D4: {"a": 1},
        D4 - timedelta(days=10): {"a": 3},
    }
    ps = player_stats(scores, "a", as_of=D4)
    assert ps.played == 5 and ps.solved == 4 and ps.points == 5 + 0 + 3 + 6 + 4
    assert ps.current_play_streak == 4 and ps.longest_play_streak == 4
    assert ps.current_solve_streak == 2 and ps.longest_solve_streak == 2
    assert ps.average_guesses == pytest.approx((2 + 4 + 1 + 3) / 4)
    assert ps.perfect == 1

    # Missing the latest day ends current streaks but not longest.
    later = player_stats(scores, "a", as_of=D4 + timedelta(days=1))
    assert later.current_play_streak == 0 and later.longest_play_streak == 4


def test_head_to_head():
    scores = {D1: {"a": 2, "b": 3}, D2: {"a": FAIL, "b": 6}, D3: {"a": 4, "b": 4}, D4: {"a": 1}}
    h = head_to_head(scores, "a", "b")
    assert (h.days, h.a_better, h.b_better, h.tied) == (3, 1, 1, 1)


# --- Sync ---

class FakeStore:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config
        self.days: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.synced: Dict[str, Dict[str, Any]] = {}
        self.overrides: Dict[str, Dict[str, Any]] = {}
        self.players: Dict[str, str] = {}
        self.status: Dict[str, Any] = {}
        self.redis_client = SimpleNamespace(redis=None)

    async def get_config(self, guild_id):
        return copy.deepcopy(self.config)

    async def save_config(self, guild_id, config):
        self.config = copy.deepcopy(config)

    async def get_synced(self, guild_id):
        return copy.deepcopy(self.synced)

    async def save_day(self, guild_id, day, results, meta):
        self.days[day] = copy.deepcopy(results)
        self.synced[day] = copy.deepcopy(meta)

    async def unmark_days(self, guild_id, days):
        for d in days:
            self.synced.pop(d, None)

    async def get_days(self, guild_id, days):
        return {d: copy.deepcopy(self.days.get(d, {})) for d in days}

    async def get_overrides(self, guild_id):
        return copy.deepcopy(self.overrides)

    async def get_players(self, guild_id):
        return dict(self.players)

    async def set_players(self, guild_id, names):
        self.players.update(names)

    async def get_status(self, guild_id):
        return dict(self.status)

    async def update_status(self, guild_id, changes):
        self.status.update(changes)


def msg(day: date, hour: int, author: str, content: str, minute: int = 0, bot: bool = False) -> ChatMessage:
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=PT)
    return ChatMessage(
        id=int(local.timestamp() * 1000) + hash(author) % 1000,
        author_id=f"id-{author}",
        author_name=author,
        created_at=local.astimezone(timezone.utc),
        content=content,
        is_bot=bot,
    )


class FakeHistory:
    def __init__(self, messages: List[ChatMessage]):
        self.messages = sorted(messages, key=lambda m: m.created_at)
        self.calls: List[tuple] = []

    def __call__(self, after, before):
        self.calls.append((after, before))

        async def gen():
            for m in self.messages:
                if after < m.created_at < before:
                    yield m
        return gen()


def now_after(day: date) -> datetime:
    """03:00 Pacific the morning after `day`."""
    nxt = day + timedelta(days=1)
    return datetime(nxt.year, nxt.month, nxt.day, 3, tzinfo=PT).astimezone(timezone.utc)


SEP15, SEP16 = date(2026, 9, 15), date(2026, 9, 16)


def config(first_day: date) -> Dict[str, Any]:
    return {"channel_id": "1", "timezone": "America/Los_Angeles", "recap": True,
            "first_day": first_day.isoformat()}


@pytest.mark.asyncio
async def test_sync_reads_a_day_with_every_kind_of_post():
    store = FakeStore(config(SEP16))
    history = FakeHistory([
        msg(SEP16, 8, "nick", share(1650, "🟥 🟩 ⬛ ⬛ ⬛ ⬛")),
        msg(SEP16, 9, "amy", "3"),
        msg(SEP16, 10, "amy", "wait no, 4"),               # LLM correction
        msg(SEP16, 11, "bob", "nada"),
        msg(SEP16, 12, "cat", share(1400, "🟩 ⬛ ⬛ ⬛ ⬛ ⬛")),  # archive puzzle
        msg(SEP16, 13, "dan", "got it on the last frame"),  # LLM
        msg(SEP16, 14, "CunningBot", "5", bot=True),
        msg(SEP15, 22, "eve", "2"),                         # a different day
    ])
    llm = AsyncMock(return_value='{"results": [{"index": 2, "score": 4}, {"index": 4, "score": 6}]}')

    report = await sync_guild(store, GUILD, history, llm, now=now_after(SEP16))

    assert report.synced_days == [SEP16]
    day = store.days["2026-09-16"]
    assert {uid: r["score"] for uid, r in day.items()} == {
        "id-nick": 2, "id-amy": 4, "id-bob": FAIL, "id-dan": 6,
    }
    assert day["id-amy"]["source"] == "llm"
    assert store.synced["2026-09-16"]["llm_failed"] is False
    assert store.players["id-nick"] == "nick"
    prompt = llm.call_args[0][1]
    assert "[already read as 3]" in prompt and "Framed #1650" in prompt


@pytest.mark.asyncio
async def test_sync_skips_llm_when_rules_read_everything():
    store = FakeStore(config(SEP16))
    history = FakeHistory([msg(SEP16, 8, "nick", "2")])
    llm = AsyncMock()
    await sync_guild(store, GUILD, history, llm, now=now_after(SEP16))
    llm.assert_not_called()


@pytest.mark.asyncio
async def test_today_is_not_read_until_it_ends():
    store = FakeStore(config(SEP16))
    history = FakeHistory([msg(SEP16, 8, "nick", "2")])
    evening = datetime(2026, 9, 16, 22, tzinfo=PT).astimezone(timezone.utc)
    report = await sync_guild(store, GUILD, history, AsyncMock(), now=evening)
    assert report.synced_days == [] and history.calls == []


@pytest.mark.asyncio
async def test_missed_days_are_caught_up_in_one_read():
    start = date(2026, 9, 10)
    store = FakeStore(config(start))
    store.synced["2026-09-10"] = {"llm_failed": False}
    history = FakeHistory([
        msg(date(2026, 9, 12), 9, "nick", "1"),
        msg(date(2026, 9, 14), 9, "nick", "2"),
        msg(SEP16, 9, "nick", "3"),
    ])
    report = await sync_guild(store, GUILD, history, AsyncMock(), now=now_after(SEP16))

    assert report.synced_days == [date(2026, 9, d) for d in range(11, 17)]
    assert len(history.calls) == 1
    assert store.days["2026-09-11"] == {}
    assert store.days["2026-09-14"]["id-nick"]["score"] == 2
    assert pending_days(store.config, store.synced, now_after(SEP16)) == []


@pytest.mark.asyncio
async def test_max_days_leaves_the_rest_for_next_time():
    store = FakeStore(config(date(2026, 9, 1)))
    report = await sync_guild(store, GUILD, FakeHistory([]), AsyncMock(), now=now_after(SEP16), max_days=5)
    assert len(report.synced_days) == 5 and report.remaining_days == 11
    report = await sync_guild(store, GUILD, FakeHistory([]), AsyncMock(), now=now_after(SEP16), max_days=31)
    assert len(report.synced_days) == 11 and report.remaining_days == 0


@pytest.mark.asyncio
async def test_llm_failure_keeps_rule_results_and_retries():
    store = FakeStore(config(SEP16))
    history = FakeHistory([msg(SEP16, 8, "nick", "2"), msg(SEP16, 9, "amy", "so close, 5 lol")])
    failing = AsyncMock(side_effect=RuntimeError("timeout"))

    report = await sync_guild(store, GUILD, history, failing, now=now_after(SEP16))
    assert report.llm_failed_days == [SEP16]
    assert list(store.days["2026-09-16"]) == ["id-nick"]
    assert pending_days(store.config, store.synced, now_after(SEP16)) == [SEP16]

    working = AsyncMock(return_value='{"results": [{"index": 1, "score": 5}]}')
    report = await sync_guild(store, GUILD, history, working, now=now_after(SEP16))
    assert report.llm_failed_days == []
    assert store.days["2026-09-16"]["id-amy"]["score"] == 5


@pytest.mark.asyncio
async def test_llm_retries_stop_after_max_attempts():
    store = FakeStore(config(SEP16))
    history = FakeHistory([msg(SEP16, 9, "amy", "hmm")])
    failing = AsyncMock(side_effect=RuntimeError("down"))
    for _ in range(MAX_LLM_ATTEMPTS):
        await sync_guild(store, GUILD, history, failing, now=now_after(SEP16))
    assert pending_days(store.config, store.synced, now_after(SEP16)) == []
    data = await stats_service.load(store, GUILD, now=now_after(SEP16))
    assert data.unreadable == [SEP16]
    assert "couldn't read" in stats_service.sync_warning(data, now=now_after(SEP16))


@pytest.mark.asyncio
async def test_backfill_extends_tracking_and_rereads():
    store = FakeStore(config(SEP16))
    store.synced["2026-09-16"] = {"llm_failed": False}
    days = await prepare_backfill(store, GUILD, date(2026, 9, 14), SEP16)
    assert days == 3
    assert store.config["first_day"] == "2026-09-14"
    assert pending_days(store.config, store.synced, now_after(SEP16)) == [
        date(2026, 9, 14), SEP15, SEP16]


# --- Loading and warnings ---

@pytest.mark.asyncio
async def test_load_applies_overrides_and_flags_pending_days():
    store = FakeStore(config(SEP15))
    store.days["2026-09-15"] = {"id-a": {"score": 3}, "id-b": {"score": 2}}
    store.synced["2026-09-15"] = {"llm_failed": False}
    store.overrides = {"2026-09-15:id-a": {"score": 1}, "2026-09-15:id-b": {"score": None}}
    now = now_after(SEP16)

    data = await stats_service.load(store, GUILD, now=now)
    assert data.scores == {SEP15: {"id-a": 1}}
    assert data.pending == [SEP16]
    assert data.as_of == SEP15   # streaks don't break on the unread day
    assert "1 day not read yet" in stats_service.sync_warning(data, now=now)
    assert "sync worker isn't running" in stats_service.sync_warning(data, now=now)

    store.status["last_worker_run_at"] = now.isoformat()
    data = await stats_service.load(store, GUILD, now=now)
    assert "isn't running" not in stats_service.sync_warning(data, now=now)


def test_parse_day_and_periods():
    latest = SEP16
    assert stats_service.parse_day(None, latest) == latest
    assert stats_service.parse_day("#1650", latest) == SEP16
    assert stats_service.parse_day("2026-01-02", latest) == date(2026, 1, 2)
    assert stats_service.parse_day("tuesday", latest) is None
    assert stats_service.resolve_period("month", latest)[:2] == (date(2026, 9, 1), latest)
    assert stats_service.resolve_period("all", latest, 2024)[:2] == (date(2024, 1, 1), date(2024, 12, 31))


def test_find_players():
    names = {"1": "Nick", "2": "Nicole", "3": "Amy"}
    assert stats_service.find_players(names, "nick") == ["1"]
    assert sorted(stats_service.find_players(names, "nic")) == ["1", "2"]
    assert stats_service.find_players(names, "zed") == []


# --- Agent tool ---

@pytest.mark.asyncio
async def test_framed_stats_tool_leaderboard_and_player():
    from bot.domain.agent.tools.framed_stats import execute_framed_stats

    store = FakeStore(config(SEP16))
    store.days["2026-09-16"] = {"id-a": {"score": 2}, "id-b": {"score": FAIL}}
    store.synced["2026-09-16"] = {"llm_failed": False}
    store.players = {"id-a": "Nick", "id-b": "Amy"}
    store.status["last_worker_run_at"] = datetime.now(timezone.utc).isoformat()
    channel = SimpleNamespace(guild=SimpleNamespace(id=123))

    with patch("bot.app.redis.framed_store.FramedRedisStore", return_value=store):
        board = await execute_framed_stats({"action": "leaderboard", "period": "all"}, channel)
        player = await execute_framed_stats({"action": "player", "player": "amy"}, channel)
        missing = await execute_framed_stats({"action": "player", "player": "zed"}, channel)

    assert "**1.** Nick — **5 pts**" in board
    assert "Framed stats for Amy" in player and "Solved:** 0%" in player
    assert "Known players: Amy, Nick" in missing


# --- Recap ---

@pytest.mark.asyncio
async def test_recap_posts_once_after_the_day_is_read():
    from bot.app import framed_runtime

    store = FakeStore(config(SEP16))
    now = datetime.now(timezone.utc)
    latest = latest_complete_day(now, PT)
    store.config["first_day"] = latest.isoformat()
    channel = SimpleNamespace(send=AsyncMock())

    with patch.object(framed_runtime, "resolve_channel", AsyncMock(return_value=channel)):
        # Not read yet: no recap.
        assert await framed_runtime.post_recap_if_due(None, store, GUILD) is False

        store.days[latest.isoformat()] = {"id-a": {"score": 2}}
        store.synced[latest.isoformat()] = {"llm_failed": False}
        store.players = {"id-a": "Nick"}
        assert await framed_runtime.post_recap_if_due(None, store, GUILD) is True
        assert await framed_runtime.post_recap_if_due(None, store, GUILD) is False

    channel.send.assert_awaited_once()
    embed = channel.send.call_args.kwargs["embed"]
    assert f"#{puzzle_for_date(latest)}" in embed.title and "Nick" in embed.description
