"""Tests for the read_channel agent tool: paging, filters, and attachment URLs."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import List, Optional
from unittest.mock import MagicMock

import discord
import pytest

from bot.domain.agent.tools.read_channel import (
    MAX_SCANNED,
    execute_read_channel,
)

GUILD_ID = 1
CHANNEL_ID = 10
BASE = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


class FakeAttachment:
    def __init__(self, filename: str, url: str) -> None:
        self.filename = filename
        self.url = url


class FakeMessage:
    def __init__(self, msg_id, author, content="", attachments=(), embeds=(), created_at=None):
        self.id = msg_id
        self.author = author
        self.content = content
        self.attachments = list(attachments)
        self.embeds = list(embeds)
        self.created_at = created_at or (BASE + timedelta(minutes=msg_id))


def author(name: str, user_id: int = 99):
    return SimpleNamespace(
        id=user_id, name=name, display_name=name, global_name=name, nick=None
    )


class FakeChannel:
    """A channel whose history() honours before/after/limit/oldest_first."""

    def __init__(self, messages: List[FakeMessage], guild=None, can_read=True):
        # Stored oldest-first; history() reverses when asked for newest-first.
        self.messages = sorted(messages, key=lambda m: m.id)
        self.name = "general"
        self.id = CHANNEL_ID
        self.guild = guild
        self._can_read = can_read
        self.history_calls: List[dict] = []

    def permissions_for(self, _member):
        return SimpleNamespace(read_message_history=self._can_read)

    def history(self, limit=None, before=None, after=None, oldest_first=False):
        self.history_calls.append(
            {"limit": limit, "before": before, "after": after, "oldest_first": oldest_first}
        )
        msgs = list(self.messages)
        if before is not None:
            cutoff = before.id if hasattr(before, "id") else None
            when = before if isinstance(before, datetime) else None
            msgs = [
                m for m in msgs
                if (cutoff is not None and m.id < cutoff) or (when is not None and m.created_at < when)
            ]
        if after is not None:
            cutoff = after.id if hasattr(after, "id") else None
            when = after if isinstance(after, datetime) else None
            msgs = [
                m for m in msgs
                if (cutoff is not None and m.id > cutoff) or (when is not None and m.created_at > when)
            ]
        if not oldest_first:
            msgs.reverse()
        if limit is not None:
            msgs = msgs[:limit]

        async def gen():
            for m in msgs:
                yield m

        return gen()


class FakeGuild:
    def __init__(self, channel: Optional[FakeChannel] = None):
        self.id = GUILD_ID
        self.me = SimpleNamespace(id=2)
        self._channel = channel
        self.text_channels = [channel] if channel else []

    def get_channel_or_thread(self, channel_id):
        if self._channel is not None and channel_id == self._channel.id:
            return self._channel
        return None


def make_channel(messages, can_read=True):
    """A stand-in that passes the tool's isinstance(discord.TextChannel) check.

    The behaviour lives in FakeChannel; the mock only supplies the type.
    """
    fake = FakeChannel(messages, can_read=can_read)
    channel = MagicMock(spec=discord.TextChannel)
    channel.name = fake.name
    channel.id = fake.id
    channel.history.side_effect = fake.history
    channel.permissions_for.side_effect = fake.permissions_for
    channel.history_calls = fake.history_calls
    guild = FakeGuild(channel)
    channel.guild = guild
    return channel


def numbered(count: int, who="alice", start=1):
    return [FakeMessage(i, author(who), content=f"message {i}") for i in range(start, start + count)]


@pytest.mark.asyncio
async def test_reads_current_channel_when_no_name_given():
    channel = make_channel(numbered(3))
    out = await execute_read_channel({}, channel)
    assert "message 1" in out and "message 3" in out


@pytest.mark.asyncio
async def test_messages_come_back_oldest_first():
    channel = make_channel(numbered(3))
    out = await execute_read_channel({}, channel)
    assert out.index("message 1") < out.index("message 2") < out.index("message 3")


@pytest.mark.asyncio
async def test_limit_is_capped_at_100():
    channel = make_channel(numbered(150))
    await execute_read_channel({"limit": 500}, channel)
    assert channel.history_calls[0]["limit"] == 100


@pytest.mark.asyncio
async def test_before_message_id_pages_backwards():
    channel = make_channel(numbered(10))
    out = await execute_read_channel({"before": "4", "limit": 50}, channel)
    assert "message 3" in out
    assert "message 4" not in out
    assert "message 5" not in out


@pytest.mark.asyncio
async def test_before_accepts_a_message_link():
    channel = make_channel(numbered(10))
    link = f"https://discord.com/channels/{GUILD_ID}/{CHANNEL_ID}/4"
    out = await execute_read_channel({"before": link, "limit": 50}, channel)
    assert "message 3" in out and "message 4" not in out


@pytest.mark.asyncio
async def test_before_accepts_an_iso_date():
    channel = make_channel(numbered(10))
    out = await execute_read_channel({"before": "2026-09-01T12:05", "limit": 50}, channel)
    assert "message 4" in out  # created 12:04
    assert "message 6" not in out


@pytest.mark.asyncio
async def test_unparseable_cursor_explains_itself():
    channel = make_channel(numbered(3))
    out = await execute_read_channel({"before": "last tuesday"}, channel)
    assert "Couldn't understand" in out
    assert not channel.history_calls


@pytest.mark.asyncio
async def test_after_alone_reads_forward_and_stays_chronological():
    channel = make_channel(numbered(10))
    out = await execute_read_channel({"after": "7", "limit": 50}, channel)
    assert channel.history_calls[0]["oldest_first"] is True
    assert "message 7" not in out
    assert out.index("message 8") < out.index("message 10")


@pytest.mark.asyncio
async def test_author_filter_matches_display_name_case_insensitively():
    msgs = numbered(3, who="alice") + [
        FakeMessage(10, author("Moop", user_id=42), content="nice photo")
    ]
    channel = make_channel(msgs)
    out = await execute_read_channel({"author": "moop"}, channel)
    assert "nice photo" in out
    assert "message 1" not in out


@pytest.mark.asyncio
async def test_author_filter_matches_a_mention_or_id():
    msgs = numbered(2) + [FakeMessage(10, author("Moop", user_id=42), content="hi")]
    for value in ("<@42>", "42"):
        out = await execute_read_channel({"author": value}, make_channel(msgs))
        assert "hi" in out and "message 1" not in out


@pytest.mark.asyncio
async def test_has_attachments_filter_and_urls_are_included():
    att = FakeAttachment("cat.png", "https://cdn.discordapp.com/attachments/1/2/cat.png")
    msgs = numbered(2) + [FakeMessage(10, author("moop"), content="look", attachments=[att])]
    channel = make_channel(msgs)
    out = await execute_read_channel({"has_attachments": True}, channel)
    assert "https://cdn.discordapp.com/attachments/1/2/cat.png" in out
    assert "cat.png" in out
    assert "message 1" not in out


@pytest.mark.asyncio
async def test_filtered_reads_scan_far_past_the_returned_limit():
    msgs = numbered(400) + [FakeMessage(500, author("moop"), content="found me")]
    channel = make_channel(msgs)
    out = await execute_read_channel({"author": "moop", "limit": 5}, channel)
    assert "found me" in out
    assert channel.history_calls[0]["limit"] == MAX_SCANNED


@pytest.mark.asyncio
async def test_footer_hands_back_a_cursor_when_more_remains():
    channel = make_channel(numbered(50))
    out = await execute_read_channel({"limit": 10}, channel)
    # Newest 10 are 41..50; the oldest one seen is 41, so that is the next cursor.
    assert "before='41'" in out
    assert "Reached the start" not in out


@pytest.mark.asyncio
async def test_footer_says_when_the_channel_start_is_reached():
    channel = make_channel(numbered(5))
    out = await execute_read_channel({"limit": 50}, channel)
    assert "Reached the start of the channel" in out


@pytest.mark.asyncio
async def test_no_matches_still_reports_where_it_got_to():
    channel = make_channel(numbered(5))
    out = await execute_read_channel({"author": "nobody"}, channel)
    assert "No matching messages" in out
    assert "Reached the start of the channel" in out


@pytest.mark.asyncio
async def test_missing_permission_is_reported():
    channel = make_channel(numbered(3), can_read=False)
    out = await execute_read_channel({}, channel)
    assert "permission" in out.lower()


@pytest.mark.asyncio
async def test_forbidden_mid_read_is_reported():
    channel = make_channel(numbered(3))

    def boom(*_args, **_kwargs):
        async def gen():
            raise discord.Forbidden(SimpleNamespace(status=403, reason="no"), "nope")
            yield  # pragma: no cover

        return gen()

    channel.history.side_effect = boom
    out = await execute_read_channel({}, channel)
    assert "permission" in out.lower()


@pytest.mark.asyncio
async def test_unknown_channel_name_lists_options():
    channel = make_channel(numbered(3))
    out = await execute_read_channel({"channel_name": "nowhere"}, channel)
    assert "Could not find a channel" in out
    assert "general" in out


@pytest.mark.asyncio
async def test_channel_lookup_by_id_reads_that_channel():
    channel = make_channel(numbered(3))
    out = await execute_read_channel({"channel_name": str(CHANNEL_ID)}, channel)
    assert "message 1" in out


@pytest.mark.asyncio
async def test_outside_a_guild_is_refused():
    channel = FakeChannel(numbered(3), guild=None)
    out = await execute_read_channel({}, channel)
    assert "not in a server" in out
