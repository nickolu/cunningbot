"""Tests for the server calendar: the regex gate, the extractor's parsing and
validation, dedup and typed-input rules, the Redis store on a fake Redis, the
capture/undo listener, the agent tools and the /calendar add and remove
commands. No network: the model is a stub that returns canned JSON."""

import datetime as dt
import json
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import discord
import pytest

from bot.app.commands.calendar.calendar import CalendarCog
from bot.app.commands.calendar.calendar_listener import (
    CAPTURE_EMOJI, UNDO_EMOJI, CalendarListenerCog,
)
from bot.app.redis.calendar_store import CalendarRedisStore
from bot.domain.agent.tools.add_calendar_event import execute_add_calendar_event
from bot.domain.agent.tools.calendar_events import execute_calendar_events
from bot.domain.calendar import service as svc
from bot.domain.calendar.extractor import (
    MIN_CONFIDENCE, SYSTEM_PROMPT, build_prompt, extract_events, parse_reply,
)
from bot.domain.calendar.gate import looks_like_date
from bot.domain.calendar.models import (
    CalendarEvent, CaptureContext, ChatLine, resolve_timezone_name,
)
from bot.domain.scan.extractor import ExtractError

PT = ZoneInfo("America/Los_Angeles")
GUILD = 111
GUILD_S = str(GUILD)
CHANNEL = 222
AUTHOR = 333
OTHER = 444
BOT_ID = 999
# A Saturday afternoon.
SENT = dt.datetime(2026, 11, 21, 14, 30, tzinfo=PT)


# ------------------------------------------------------------------ fake redis


def _bound(value):
    """Parse a zrangebyscore bound: float, '(x' exclusive, '-inf', '+inf'."""
    if isinstance(value, (int, float)):
        return float(value), True
    text = str(value)
    if text in ("-inf", "+inf"):
        return float(text), True
    if text.startswith("("):
        return float(text[1:]), False
    return float(text), True


class FakeRedis:
    """The slice of redis.asyncio the calendar store uses, in dicts."""

    def __init__(self):
        self.kv: Dict[str, str] = {}
        self.hashes: Dict[str, Dict[str, str]] = {}
        self.zsets: Dict[str, Dict[str, float]] = {}
        self.sets: Dict[str, set] = {}
        self.ttl: Dict[str, int] = {}

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, value):
        self.kv[key] = value

    async def delete(self, *keys):
        n = 0
        for key in keys:
            for table in (self.kv, self.hashes, self.zsets, self.sets):
                if key in table:
                    del table[key]
                    n += 1
        return n

    async def expire(self, key, seconds):
        self.ttl[key] = seconds
        return True

    async def incr(self, key):
        self.kv[key] = str(int(self.kv.get(key, "0")) + 1)
        return int(self.kv[key])

    async def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value
        return 1

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def hdel(self, key, *fields):
        table = self.hashes.get(key, {})
        return sum(1 for f in fields if table.pop(f, None) is not None)

    async def hexists(self, key, field):
        return field in self.hashes.get(key, {})

    async def hmget(self, key, fields):
        table = self.hashes.get(key, {})
        return [table.get(f) for f in fields]

    async def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)

    async def zrem(self, key, *members):
        table = self.zsets.get(key, {})
        return sum(1 for m in members if table.pop(m, None) is not None)

    async def zrangebyscore(self, key, lo, hi):
        lo_v, lo_inc = _bound(lo)
        hi_v, hi_inc = _bound(hi)
        out = []
        for member, score in sorted(self.zsets.get(key, {}).items(), key=lambda kv: (kv[1], kv[0])):
            if (score > lo_v or (lo_inc and score == lo_v)) and (score < hi_v or (hi_inc and score == hi_v)):
                out.append(member)
        return out

    async def zcount(self, key, lo, hi):
        return len(await self.zrangebyscore(key, lo, hi))

    async def sadd(self, key, *members):
        self.sets.setdefault(key, set()).update(members)
        return len(members)

    async def srem(self, key, *members):
        table = self.sets.get(key, set())
        return sum(1 for m in members if m in table and not table.discard(m))

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.calls: List[Any] = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self
        return record

    async def execute(self):
        results = []
        for name, args, kwargs in self.calls:
            results.append(await getattr(self.redis, name)(*args, **kwargs))
        self.calls = []
        return results


def make_store() -> CalendarRedisStore:
    store = CalendarRedisStore.__new__(CalendarRedisStore)
    store.redis = FakeRedis()
    store.redis_client = None
    return store


def event(**over) -> CalendarEvent:
    base = dict(id="abc123", title="Game night", date="2026-11-27", time="20:00",
                timezone="America/Los_Angeles", author_id=str(AUTHOR), author_name="Sam",
                message_id="5001", channel_id=str(CHANNEL))
    base.update(over)
    return CalendarEvent(**base)


# ------------------------------------------------------------------------ gate


class TestGate:
    @pytest.mark.parametrize("text", [
        "game night friday at 8?",
        "Thanksgiving dinner is on the 26th, bring pie",
        "leaving for Tahoe 12/19, back on the 23rd",
        "patch drops Nov 30",
        "tomorrow at noon works",
        "let's do it next weekend",
        "raid at 7:30pm",
        "release date 2026-12-05",
        "lunch Tues?",
    ])
    def test_lets_dates_through(self, text):
        assert looks_like_date(text)

    @pytest.mark.parametrize("text", [
        "lol",
        "same",
        "I may go, not sure yet",
        "he just sat there and marched on",
        "https://example.com/some/link",
        "that's a great idea honestly",
        "gg everyone",
    ])
    def test_drops_chatter(self, text):
        assert not looks_like_date(text)


# ------------------------------------------------------------------- extractor


def context(text="game night friday at 8?", previous=(), sent=SENT) -> CaptureContext:
    return CaptureContext(
        message=ChatLine("Sam", text, sent),
        previous=tuple(ChatLine("Alex", p, sent - dt.timedelta(minutes=5)) for p in previous),
        timezone="America/Los_Angeles",
    )


def reply(*events) -> str:
    return json.dumps({"events": list(events)})


class TestExtractor:
    def test_prompt_states_the_sent_day_in_local_time(self):
        prompt = build_prompt(context(previous=("anyone free this week?",)))
        assert "Saturday" in prompt and "2026-11-21" in prompt and "14:30" in prompt
        assert "America/Los_Angeles" in prompt
        assert "anyone free this week?" in prompt
        assert "game night friday at 8?" in prompt

    def test_parses_a_good_event(self):
        got = parse_reply(reply({"title": "Game night", "date": "2026-11-27", "time": "20:00",
                                 "end_date": None, "confidence": 0.9}), context())
        assert len(got) == 1
        assert (got[0].title, got[0].date, got[0].time, got[0].end_date) == ("Game night", "2026-11-27", "20:00", None)

    def test_drops_past_low_confidence_and_far_future(self):
        got = parse_reply(reply(
            {"title": "Old", "date": "2026-11-20", "confidence": 0.9},
            {"title": "Unsure", "date": "2026-11-28", "confidence": MIN_CONFIDENCE - 0.1},
            {"title": "Far", "date": "2031-01-01", "confidence": 0.9},
            {"title": "Keep", "date": "2026-11-28", "confidence": 0.8},
        ), context())
        assert [e.title for e in got] == ["Keep"]

    def test_today_counts_as_upcoming(self):
        got = parse_reply(reply({"title": "Tonight", "date": "2026-11-21", "time": "19:00", "confidence": 0.9}), context())
        assert got and got[0].date == "2026-11-21"

    def test_bad_time_and_bad_end_date_are_dropped_not_fatal(self):
        got = parse_reply(reply({"title": "Trip", "date": "2026-12-19", "time": "evening",
                                 "end_date": "2026-12-10", "confidence": 0.9}), context())
        assert got[0].time is None and got[0].end_date is None

    def test_end_date_after_start_is_kept(self):
        got = parse_reply(reply({"title": "Trip", "date": "2026-12-19", "end_date": "2026-12-23", "confidence": 0.9}), context())
        assert got[0].end_date == "2026-12-23"

    def test_fenced_json_and_duplicates_within_reply(self):
        text = "```json\n" + reply(
            {"title": "Game night", "date": "2026-11-27", "confidence": 0.9},
            {"title": "game night", "date": "2026-11-27", "confidence": 0.8},
        ) + "\n```"
        assert len(parse_reply(text, context())) == 1

    def test_unreadable_reply_raises(self):
        with pytest.raises(ExtractError):
            parse_reply("sure thing!", context())
        with pytest.raises(ExtractError):
            parse_reply('{"items": []}', context())

    @pytest.mark.asyncio
    async def test_extract_events_uses_the_given_llm(self):
        seen = {}

        async def llm(system, prompt):
            seen["system"], seen["prompt"] = system, prompt
            return reply({"title": "Game night", "date": "2026-11-27", "confidence": 0.9})

        got = await extract_events(context(), llm)
        assert seen["system"] == SYSTEM_PROMPT and "game night friday" in seen["prompt"]
        assert got[0].title == "Game night"

    @pytest.mark.asyncio
    async def test_llm_failure_becomes_extract_error(self):
        async def llm(system, prompt):
            raise RuntimeError("boom")
        with pytest.raises(ExtractError):
            await extract_events(context(), llm)


# --------------------------------------------------------------------- service


class TestService:
    def test_duplicate_same_day_overlapping_title(self):
        existing = [event(title="Game night at Sam's", date="2026-11-27")]
        assert svc.is_duplicate("game night", "2026-11-27", existing) is existing[0]
        assert svc.is_duplicate("Board game night", "2026-11-27", existing) is existing[0]
        assert svc.is_duplicate("Game stream", "2026-11-27", existing) is None
        assert svc.is_duplicate("game night", "2026-11-28", existing) is None
        assert svc.is_duplicate("Dentist", "2026-11-27", existing) is None

    def test_parse_date_input(self):
        today = dt.date(2026, 11, 21)
        assert svc.parse_date_input("2026-12-05", today) == dt.date(2026, 12, 5)
        assert svc.parse_date_input("12/5", today) == dt.date(2026, 12, 5)
        assert svc.parse_date_input("1/5", today) == dt.date(2027, 1, 5)   # rolls to next year
        assert svc.parse_date_input("1/5/26", today) == dt.date(2026, 1, 5)
        assert svc.parse_date_input("tomorrow", today) == dt.date(2026, 11, 22)
        assert svc.parse_date_input("friday", today) is None
        assert svc.parse_date_input("13/40", today) is None

    def test_parse_time_input(self):
        assert svc.parse_time_input("7pm") == "19:00"
        assert svc.parse_time_input("7:30 PM") == "19:30"
        assert svc.parse_time_input("19:00") == "19:00"
        assert svc.parse_time_input("12am") == "00:00"
        assert svc.parse_time_input("12pm") == "12:00"
        assert svc.parse_time_input(None) is None
        assert svc.parse_time_input("evening") == ""
        assert svc.parse_time_input("25:00") == ""

    def test_render_groups_by_day_and_marks_today(self):
        now = SENT
        events = [
            event(id="aaa111", title="Raid", date="2026-11-22", time="19:30"),
            event(id="bbb222", title="Game night", date="2026-11-21", time=None),
            event(id="ccc333", title="Tahoe", date="2026-11-22", time=None, end_date="2026-11-25"),
        ]
        text = svc.render_upcoming(events, now, "America/Los_Angeles")
        lines = text.split("\n")
        assert lines[0] == "**Sat Nov 21 (today)**"
        assert "Game night" in lines[1] and "`bbb222`" in lines[1]
        assert lines[2] == "**Sun Nov 22 (tomorrow)**"
        assert "through Nov 25" in lines[3]          # all-day entry sorts before the timed one
        assert lines[4].startswith("• 7:30pm — Raid")

    def test_upcoming_window_starts_at_local_midnight(self):
        start, end = svc.upcoming_window(SENT, "America/Los_Angeles", 7)
        assert start == dt.datetime(2026, 11, 21, tzinfo=PT).timestamp()
        assert end - start == 7 * 86400

    def test_timezone_names(self):
        assert resolve_timezone_name("pacific") == "America/Los_Angeles"
        assert resolve_timezone_name("Europe/London") == "Europe/London"
        assert resolve_timezone_name("") == "America/Los_Angeles"
        assert resolve_timezone_name("Narnia/Lamppost") is None

    def test_event_record_roundtrip_and_start(self):
        e = event()
        again = CalendarEvent.from_record(json.loads(json.dumps(e.to_record())))
        assert again == e
        assert e.start() == dt.datetime(2026, 11, 27, 20, 0, tzinfo=PT)
        assert event(time=None).start().hour == 0


# ----------------------------------------------------------------------- store


@pytest.mark.asyncio
async def test_store_add_query_and_delete_by_message():
    store = make_store()
    a = event(id="a1", date="2026-11-22", message_id="5001")
    b = event(id="b2", title="Raid", date="2026-11-25", message_id="5001")
    c = event(id="c3", title="Later", date="2027-01-10", message_id="5002")
    for e in (a, b, c):
        await store.add_event(GUILD_S, e)

    start, end = svc.upcoming_window(SENT, "America/Los_Angeles", 7)
    assert [e.id for e in await store.get_events_between(GUILD_S, start, end)] == ["a1", "b2"]
    assert [e.id for e in await store.get_events_on(GUILD_S, "2026-11-25", "America/Los_Angeles")] == ["b2"]
    assert await store.count_upcoming(GUILD_S, start) == 3

    removed = await store.delete_events_for_message(GUILD_S, "5001")
    assert sorted(e.id for e in removed) == ["a1", "b2"]
    assert await store.get_event(GUILD_S, "a1") is None
    assert [e.id for e in await store.get_events_between(GUILD_S, 0, 1e12)] == ["c3"]
    assert await store.get_events_for_message(GUILD_S, "5001") == []


@pytest.mark.asyncio
async def test_store_tolerates_index_entry_without_record_and_trims_past():
    store = make_store()
    await store.add_event(GUILD_S, event(id="gone", date="2026-11-22"))
    await store.add_event(GUILD_S, event(id="old", date="2026-01-01"))
    await store.redis.hdel(store._events_key(GUILD_S), "gone")   # evicted record
    assert await store.get_events_between(GUILD_S, 0, 1e12) == [event(id="old", date="2026-01-01")]
    assert "gone" not in store.redis.zsets[store._index_key(GUILD_S)]
    assert await store.trim_past(GUILD_S, svc.past_cutoff_epoch(SENT)) == 1
    assert await store.get_event(GUILD_S, "old") is None


@pytest.mark.asyncio
async def test_store_config_defaults_and_budget():
    store = make_store()
    assert await store.get_config(GUILD_S) == {"enabled": False, "timezone": "America/Los_Angeles"}
    await store.save_config(GUILD_S, {"enabled": True, "timezone": "Europe/London"})
    assert await store.is_enabled(GUILD_S)
    assert await store.spend_budget(GUILD_S, "20261121") == 1
    assert await store.spend_budget(GUILD_S, "20261121") == 2
    assert await store.spend_budget(GUILD_S, "20261122") == 1


# -------------------------------------------------------------------- listener


def HistoryChannel(earlier=()):
    """A TextChannel double whose history() yields the given earlier messages."""
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = CHANNEL
    earlier = list(earlier)

    def history(limit=None, before=None, **_):
        page = earlier[:limit]

        async def gen():
            for m in page:
                yield m
        return gen()

    channel.history = history
    return channel


def make_message(content, channel=None, author_id=AUTHOR, message_id=5001, bot=False, sent=SENT):
    message = MagicMock(spec=discord.Message)
    message.id = message_id
    message.content = content
    message.author = SimpleNamespace(id=author_id, bot=bot, display_name="Sam" if author_id == AUTHOR else "Alex")
    message.guild = SimpleNamespace(id=GUILD)
    message.channel = channel or HistoryChannel()
    message.created_at = sent.astimezone(dt.timezone.utc)
    message.jump_url = f"https://discord.com/channels/{GUILD}/{CHANNEL}/{message_id}"
    message.add_reaction = AsyncMock()
    return message


def make_listener(store, llm_reply=None):
    calls = []

    async def llm(system, prompt):
        calls.append(prompt)
        if isinstance(llm_reply, Exception):
            raise llm_reply
        return llm_reply or reply()

    bot = MagicMock()
    bot.user = SimpleNamespace(id=BOT_ID)
    cog = CalendarListenerCog(bot, llm=llm)
    cog._store = store
    cog.llm_calls = calls
    return cog


async def enabled_store():
    store = make_store()
    await store.save_config(GUILD_S, {"enabled": True, "timezone": "America/Los_Angeles"})
    return store


GOOD_REPLY = reply({"title": "Game night", "date": "2026-11-27", "time": "20:00", "confidence": 0.9})


@pytest.mark.asyncio
async def test_capture_saves_event_and_reacts():
    store = await enabled_store()
    cog = make_listener(store, GOOD_REPLY)
    channel = HistoryChannel(earlier=[make_message("anyone around this week?", author_id=OTHER, message_id=4999)])
    message = make_message("game night friday at 8?", channel=channel)

    await cog.on_message(message)

    message.add_reaction.assert_awaited_once_with(CAPTURE_EMOJI)
    assert len(cog.llm_calls) == 1 and "anyone around this week?" in cog.llm_calls[0]
    events = await store.get_events_for_message(GUILD_S, "5001")
    assert len(events) == 1
    e = events[0]
    assert (e.title, e.date, e.time, e.author_id, e.source) == ("Game night", "2026-11-27", "20:00", str(AUTHOR), "capture")
    assert e.jump_url == message.jump_url and e.confidence == 0.9


@pytest.mark.asyncio
async def test_no_model_call_when_gate_misses_or_guild_disabled_or_author_is_bot():
    enabled = await enabled_store()
    cog = make_listener(enabled, GOOD_REPLY)
    await cog.on_message(make_message("lol same"))
    await cog.on_message(make_message("game night friday at 8?", bot=True))
    assert cog.llm_calls == []

    disabled = make_store()
    cog = make_listener(disabled, GOOD_REPLY)
    message = make_message("game night friday at 8?")
    await cog.on_message(message)
    assert cog.llm_calls == [] and not message.add_reaction.called


@pytest.mark.asyncio
async def test_nothing_found_means_no_reaction():
    store = await enabled_store()
    cog = make_listener(store, reply())
    message = make_message("I was at the dentist on Friday, awful")
    await cog.on_message(message)
    assert len(cog.llm_calls) == 1
    assert not message.add_reaction.called
    assert await store.get_events_for_message(GUILD_S, "5001") == []


@pytest.mark.asyncio
async def test_duplicate_of_existing_event_is_not_saved_again():
    store = await enabled_store()
    await store.add_event(GUILD_S, event(id="first", title="Game night at Sam's", date="2026-11-27", message_id="4000"))
    cog = make_listener(store, GOOD_REPLY)
    message = make_message("so game night is friday right", message_id=5002)
    await cog.on_message(message)
    assert not message.add_reaction.called
    assert await store.get_events_for_message(GUILD_S, "5002") == []
    assert await store.count_upcoming(GUILD_S, 0) == 1


@pytest.mark.asyncio
async def test_extract_failure_is_swallowed():
    store = await enabled_store()
    cog = make_listener(store, RuntimeError("api down"))
    message = make_message("game night friday at 8?")
    await cog.on_message(message)   # no raise
    assert not message.add_reaction.called


@pytest.mark.asyncio
async def test_budget_stops_model_calls_for_the_day():
    store = await enabled_store()
    day = svc.local_day(SENT.astimezone(dt.timezone.utc), "America/Los_Angeles")
    for _ in range(svc.DAILY_MODEL_BUDGET):
        await store.spend_budget(GUILD_S, day)
    cog = make_listener(store, GOOD_REPLY)
    await cog.on_message(make_message("game night friday at 8?"))
    assert cog.llm_calls == []


def reaction(user_id, emoji=UNDO_EMOJI, message_id=5001, manage_messages=False):
    member = SimpleNamespace(guild_permissions=SimpleNamespace(manage_messages=manage_messages))
    return SimpleNamespace(emoji=emoji, guild_id=GUILD, channel_id=CHANNEL, message_id=message_id,
                           user_id=user_id, member=member)


async def captured_store():
    store = await enabled_store()
    await store.add_event(GUILD_S, event(id="e1", message_id="5001"))
    await store.add_event(GUILD_S, event(id="e2", title="Raid", date="2026-11-28", message_id="5001"))
    return store


@pytest.mark.asyncio
async def test_author_undo_removes_events_and_the_capture_reaction():
    store = await captured_store()
    cog = make_listener(store)
    fetched = MagicMock()
    fetched.remove_reaction = AsyncMock()
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=fetched)
    cog.bot.get_channel = MagicMock(return_value=channel)

    await cog.on_raw_reaction_add(reaction(AUTHOR))

    assert await store.get_events_for_message(GUILD_S, "5001") == []
    assert await store.get_event(GUILD_S, "e2") is None
    fetched.remove_reaction.assert_awaited_once_with(CAPTURE_EMOJI, cog.bot.user)


@pytest.mark.asyncio
async def test_stranger_cannot_undo_but_a_moderator_can():
    store = await captured_store()
    cog = make_listener(store)
    cog._remove_capture_reaction = AsyncMock()

    await cog.on_raw_reaction_add(reaction(OTHER))
    assert len(await store.get_events_for_message(GUILD_S, "5001")) == 2

    await cog.on_raw_reaction_add(reaction(OTHER, manage_messages=True))
    assert await store.get_events_for_message(GUILD_S, "5001") == []


@pytest.mark.asyncio
async def test_other_emoji_and_unknown_message_are_ignored():
    store = await captured_store()
    cog = make_listener(store)
    cog._remove_capture_reaction = AsyncMock()
    await cog.on_raw_reaction_add(reaction(AUTHOR, emoji="👍"))
    await cog.on_raw_reaction_add(reaction(AUTHOR, message_id=7777))
    assert len(await store.get_events_for_message(GUILD_S, "5001")) == 2
    assert not cog._remove_capture_reaction.called


@pytest.mark.asyncio
async def test_deleting_the_source_message_removes_its_events():
    store = await captured_store()
    cog = make_listener(store)
    await cog.on_raw_message_delete(SimpleNamespace(guild_id=GUILD, message_id=5001, channel_id=CHANNEL))
    assert await store.get_events_for_message(GUILD_S, "5001") == []
    assert await store.count_upcoming(GUILD_S, 0) == 0


# ------------------------------------------------------------------- agent tools


def tool_channel():
    return SimpleNamespace(id=CHANNEL, guild=SimpleNamespace(id=GUILD))


@pytest.mark.asyncio
async def test_calendar_events_tool_lists_with_today_header():
    store = await enabled_store()
    soon = (dt.datetime.now(PT) + dt.timedelta(days=2)).date().isoformat()
    await store.add_event(GUILD_S, event(id="e1", title="Game night", date=soon))
    await store.add_event(GUILD_S, event(id="e2", title="Dentist", date=soon, time="09:00"))
    with patch("bot.domain.agent.tools.calendar_events.CalendarRedisStore", return_value=store):
        text = await execute_calendar_events({"days": 7}, tool_channel())
        only = await execute_calendar_events({"query": "dentist"}, tool_channel())
        none = await execute_calendar_events({"query": "nothing"}, tool_channel())
    assert text.startswith("Today is ") and "Game night" in text and "`e1`" in text
    assert "Dentist" in only and "Game night" not in only
    assert "Nothing matching 'nothing'" in none


@pytest.mark.asyncio
async def test_add_calendar_event_tool_validates_and_adds():
    store = make_store()
    user = SimpleNamespace(id=AUTHOR, display_name="Sam")
    future = (dt.datetime.now(PT) + dt.timedelta(days=10)).date().isoformat()
    with patch("bot.domain.agent.tools.add_calendar_event.CalendarRedisStore", return_value=store):
        past = await execute_add_calendar_event({"title": "Old", "date": "2020-01-01"}, tool_channel(), user)
        words = await execute_add_calendar_event({"title": "X", "date": "Friday"}, tool_channel(), user)
        ok = await execute_add_calendar_event({"title": "Tahoe trip", "date": future, "time": "09:00"}, tool_channel(), user)
        again = await execute_add_calendar_event({"title": "Tahoe trip", "date": future}, tool_channel(), user)
    assert "in the past" in past and "Today is" in words
    assert ok.startswith("Added to the calendar") and "Tahoe trip" in ok
    assert again.startswith("Already on the calendar")
    events = await store.get_events_between(GUILD_S, 0, 1e12)
    assert len(events) == 1 and events[0].author_id == str(AUTHOR) and events[0].source == "manual"


# --------------------------------------------------------------------- the cog


def make_interaction(user_id=AUTHOR, manage_messages=False):
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild_id = GUILD
    interaction.channel_id = CHANNEL
    interaction.user = SimpleNamespace(
        id=user_id, display_name="Sam",
        guild_permissions=SimpleNamespace(manage_messages=manage_messages),
    )
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def sent_text(interaction) -> str:
    args, kwargs = interaction.followup.send.call_args
    if args:
        return str(args[0])
    embed = kwargs.get("embed")
    return (embed.description or "") if embed is not None else str(kwargs)


def make_cog(store):
    cog = CalendarCog(MagicMock())
    cog._store = store
    return cog


@pytest.mark.asyncio
async def test_add_command_then_remove_respects_ownership():
    store = make_store()
    cog = make_cog(store)
    future = (dt.datetime.now(PT) + dt.timedelta(days=3)).date()

    bad = make_interaction()
    await cog.add.callback(cog, bad, title="Trip", date="someday")
    assert "couldn't read" in sent_text(bad)

    ok = make_interaction()
    await cog.add.callback(cog, ok, title="Tahoe trip", date=future.isoformat(), time="9am")
    assert sent_text(ok).startswith("📅 Added") and "9am" in sent_text(ok)
    (saved,) = await store.get_events_between(GUILD_S, 0, 1e12)
    assert saved.time == "09:00" and saved.source == "manual"

    stranger = make_interaction(user_id=OTHER)
    await cog.remove.callback(cog, stranger, id=saved.id)
    assert "Only Sam" in sent_text(stranger)
    assert await store.get_event(GUILD_S, saved.id) is not None

    mod = make_interaction(user_id=OTHER, manage_messages=True)
    await cog.remove.callback(cog, mod, id=f"`{saved.id}`")
    assert sent_text(mod).startswith("🗑️ Removed")
    assert await store.get_event(GUILD_S, saved.id) is None


@pytest.mark.asyncio
async def test_enable_rejects_unknown_timezone_and_sets_known_one():
    store = make_store()
    cog = make_cog(store)
    bad = make_interaction()
    await cog.enable.callback(cog, bad, timezone="Narnia/Lamppost")
    assert "don't know the timezone" in sent_text(bad)
    assert not await store.is_enabled(GUILD_S)

    ok = make_interaction()
    await cog.enable.callback(cog, ok, timezone="eastern")
    config = await store.get_config(GUILD_S)
    assert config["enabled"] and config["timezone"] == "America/New_York"
    assert "America/New_York" in sent_text(ok)

    await cog.disable.callback(cog, make_interaction())
    assert not await store.is_enabled(GUILD_S)
    assert (await store.get_config(GUILD_S))["timezone"] == "America/New_York"
