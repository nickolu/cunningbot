"""Tests for the search_news agent tool and the store method behind it."""

import fnmatch
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bot.app.redis.rss_store import RSSRedisStore
from bot.domain.agent.tools import search_news
from bot.domain.agent.tools.search_news import (
    execute_search_news,
    match_stories,
    query_terms,
)

GUILD_A = 111111111111111111
GUILD_B = 222222222222222222


class FakeRedis:
    """Just enough of redis.asyncio for the story_history code paths."""

    def __init__(self):
        self.zsets = {}
        self.strings = {}

    async def zadd(self, key, mapping):
        zset = self.zsets.setdefault(key, {})
        added = sum(1 for m in mapping if m not in zset)
        zset.update(mapping)
        return added

    async def zrangebyscore(self, key, min, max):
        zset = self.zsets.get(key, {})
        hi = float("inf") if max == "+inf" else float(max)
        return [m for m, s in sorted(zset.items(), key=lambda kv: kv[1]) if float(min) <= s <= hi]

    async def scan(self, cursor, match=None, count=None):
        keys = list(self.zsets) + list(self.strings)
        return 0, [k for k in keys if match is None or fnmatch.fnmatchcase(k, match)]


@pytest.fixture
def store():
    fake = FakeRedis()
    with patch(
        "bot.app.redis.rss_store.get_redis_client",
        return_value=SimpleNamespace(redis=fake),
    ):
        yield RSSRedisStore()


def _ago(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _story(title, summary="", hours_ago=1, urls=None):
    return {
        "title": title,
        "summary": summary,
        "article_urls": urls or ["https://example.com/" + title.split()[0].lower()],
        "posted_at": _ago(hours_ago),
        "edition": "Morning",
    }


def _channel(guild_id):
    return SimpleNamespace(guild=SimpleNamespace(id=guild_id))


# --- store ---


@pytest.mark.asyncio
async def test_story_history_channels_are_scoped_to_the_guild(store):
    await store.add_stories_to_history(str(GUILD_A), 10, [_story("A one")])
    await store.add_stories_to_history(str(GUILD_A), 20, [_story("A two")])
    await store.add_stories_to_history(str(GUILD_B), 30, [_story("B one")])
    store.redis.strings[f"rss:{GUILD_A}:summary:40:last"] = "{}"

    assert await store.get_story_history_channels(str(GUILD_A)) == ["10", "20"]
    assert await store.get_story_history_channels(str(GUILD_B)) == ["30"]
    assert await store.get_story_history_channels("333") == []


@pytest.mark.asyncio
async def test_story_history_channels_rejects_non_numeric_guild(store):
    await store.add_stories_to_history(str(GUILD_A), 10, [_story("A one")])
    assert await store.get_story_history_channels("*") == []
    assert await store.get_story_history_channels("global") == []


# --- matching ---


def test_query_terms_drop_filler_but_not_everything():
    assert query_terms("Is there any news about the Fed?") == ["fed"]
    assert query_terms("latest news") == ["latest", "news"]
    assert query_terms("   ") == []


def test_match_is_case_insensitive_and_checks_summary():
    stories = [
        _story("Markets rally", "The FED held rates steady."),
        _story("Local sports roundup", "Nothing about rates."),
    ]
    matches, partial = match_stories(stories, "fed")
    assert [s["title"] for s in matches] == ["Markets rally"]
    assert partial is False


def test_match_is_word_prefix_not_substring():
    stories = [_story("Tariffs expand", "New tariffs announced"), _story("He said so")]
    assert [s["title"] for s in match_stories(stories, "tariff")[0]] == ["Tariffs expand"]
    assert match_stories(stories, "ai")[0] == []


def test_multi_word_requires_all_terms_newest_first():
    stories = [
        _story("Apple lawsuit filed", hours_ago=30),
        _story("Apple ships phone", hours_ago=2),
        _story("New Apple lawsuit ruling", hours_ago=5),
    ]
    matches, partial = match_stories(stories, "apple lawsuit")
    assert [s["title"] for s in matches] == ["New Apple lawsuit ruling", "Apple lawsuit filed"]
    assert partial is False


def test_falls_back_to_partial_matches_when_nothing_matches_every_term():
    stories = [_story("Apple ships phone"), _story("Weather is nice")]
    matches, partial = match_stories(stories, "apple lawsuit")
    assert [s["title"] for s in matches] == ["Apple ships phone"]
    assert partial is True


def test_duplicate_titles_across_channels_are_shown_once():
    stories = [_story("Same story", hours_ago=3), _story("Same story", hours_ago=1)]
    assert len(match_stories(stories, "story")[0]) == 1


def test_breaking_news_entries_are_searchable():
    story = {
        "title": "Quake hits coast",
        "articles": [{"link": "https://example.com/quake", "summary": "A magnitude 6"}],
        "posted_at": _ago(1),
        "is_breaking_news": True,
    }
    assert match_stories([story], "magnitude")[0] == [story]
    assert "https://example.com/quake" in search_news.format_story(story)


# --- executor ---


@pytest.mark.asyncio
async def test_tool_formats_results(store):
    await store.add_stories_to_history(
        str(GUILD_A), 10,
        [_story("Fed holds rates", "Officials " + "word " * 100, urls=["https://a.example/1", "https://a.example/2"])],
    )
    result = await execute_search_news({"query": "fed"}, _channel(GUILD_A))

    assert result.startswith("1 story matched 'fed', newest first.")
    assert "- Fed holds rates (" in result
    assert "https://a.example/1 https://a.example/2" in result
    assert "…" in result  # long summary truncated
    assert "7 days" in result


@pytest.mark.asyncio
async def test_tool_caps_results(store):
    stories = [_story(f"Fed story {i}", hours_ago=i + 1) for i in range(15)]
    await store.add_stories_to_history(str(GUILD_A), 10, stories)
    result = await execute_search_news({"query": "fed"}, _channel(GUILD_A))

    assert "15 stories matched" in result
    assert "showing the newest 10" in result
    assert result.count("\n- ") == 10
    assert "Fed story 0 " in result and "Fed story 14" not in result


@pytest.mark.asyncio
async def test_tool_no_match_suggests_web_search(store):
    await store.add_stories_to_history(str(GUILD_A), 10, [_story("Fed holds rates")])
    result = await execute_search_news({"query": "volcano"}, _channel(GUILD_A))

    assert "No stories" in result
    assert "web_search" in result
    assert "7 days" in result


@pytest.mark.asyncio
async def test_tool_ignores_stories_older_than_a_week(store):
    await store.add_stories_to_history(str(GUILD_A), 10, [_story("Fed old", hours_ago=200)])
    result = await execute_search_news({"query": "fed"}, _channel(GUILD_A))
    assert "No stories" in result


@pytest.mark.asyncio
async def test_tool_never_returns_another_guilds_stories(store):
    await store.add_stories_to_history(str(GUILD_A), 10, [_story("Fed story for A")])
    await store.add_stories_to_history(str(GUILD_B), 10, [_story("Fed story for B")])

    result = await execute_search_news({"query": "fed"}, _channel(GUILD_B))
    assert "Fed story for B" in result
    assert "Fed story for A" not in result


@pytest.mark.asyncio
async def test_tool_does_not_raise_on_store_failure():
    with patch("bot.app.redis.rss_store.get_redis_client", side_effect=RuntimeError("down")):
        result = await execute_search_news({"query": "fed"}, _channel(GUILD_A))
    assert "web_search" in result


@pytest.mark.asyncio
async def test_tool_rejects_empty_query_and_missing_guild():
    assert "No search query" in await execute_search_news({"query": " "}, _channel(GUILD_A))
    assert "server" in await execute_search_news({"query": "x"}, SimpleNamespace(guild=None))


def test_tool_is_default_enabled_and_in_backfill():
    from bot.app.redis.migrations.backfill_agent_tools import DEFAULT_TOOLS_TO_ADD
    from bot.domain.agent.tools.registry import CHANNEL_AWARE_TOOLS, DEFAULT_ENABLED_TOOLS

    assert "search_news" in DEFAULT_ENABLED_TOOLS
    assert "search_news" in CHANNEL_AWARE_TOOLS
    assert "search_news" in DEFAULT_TOOLS_TO_ADD
