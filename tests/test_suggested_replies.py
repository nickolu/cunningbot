"""Tests for suggested-reply buttons: the tool, the store, posting, and clicks."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.app import suggested_replies
from bot.app.agent_runtime import AGENT_CHANNEL_LOCKS, get_agent_channel_lock
from bot.app.commands.help import HELP_PAGES
from bot.app.redis.suggestion_store import SUGGESTION_TTL_SECONDS, SuggestionRedisStore
from bot.app.suggested_replies import (
    EXPIRED_MESSAGE,
    PAUSED_MESSAGE,
    SuggestionButton,
    handle_click,
    send_agent_reply,
)
from bot.domain.agent.suggestions import collect_suggestions, offer_suggestions
from bot.domain.agent.tools.suggest_replies import execute_suggest_replies

GUILD = 42
CHANNEL = 555


@pytest.fixture(autouse=True)
def clear_locks():
    AGENT_CHANNEL_LOCKS.clear()
    yield
    AGENT_CHANNEL_LOCKS.clear()


# --- fakes -------------------------------------------------------------


class FakePipeline:
    def __init__(self, redis: "FakeRedis") -> None:
        self.redis = redis
        self.queued: List[Any] = []

    def __getattr__(self, name: str) -> Any:
        def queue(*args: Any, **kwargs: Any) -> "FakePipeline":
            self.queued.append((name, args, kwargs))
            return self
        return queue

    async def execute(self) -> List[Any]:
        results = [await getattr(self.redis, n)(*a, **k) for n, a, k in self.queued]
        self.queued = []
        return results

    async def __aenter__(self) -> "FakePipeline":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class FakeRedis:
    """Just enough of redis.asyncio for the suggestion store."""

    def __init__(self) -> None:
        self.hashes: Dict[str, Dict[str, str]] = {}
        self.expiries: Dict[str, int] = {}

    async def hgetall(self, key: str) -> Dict[str, str]:
        return dict(self.hashes.get(key, {}))

    async def hset(self, key: str, mapping: Dict[str, str]) -> int:
        self.hashes.setdefault(key, {}).update(mapping)
        return len(mapping)

    async def delete(self, *keys: str) -> int:
        return sum(1 for k in keys if self.hashes.pop(k, None) is not None)

    async def expire(self, key: str, seconds: int) -> bool:
        self.expiries[key] = seconds
        return key in self.hashes

    async def eval(self, script: str, numkeys: int, key: str, nonce: str) -> Optional[str]:
        # The claim script: compare the nonce, then take the options and delete.
        record = self.hashes.get(key)
        if record is None or record.get("nonce") != nonce:
            return None
        del self.hashes[key]
        return record["options"]

    def pipeline(self, transaction: bool = False) -> FakePipeline:
        return FakePipeline(self)


def make_store(redis: FakeRedis) -> SuggestionRedisStore:
    with patch(
        "bot.app.redis.suggestion_store.get_redis_client",
        return_value=SimpleNamespace(redis=redis),
    ):
        return SuggestionRedisStore()


def make_channel(history_newest_first: Optional[List[Any]] = None) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = CHANNEL
    sent: List[Any] = []

    async def send(content: str, **kwargs: Any) -> Any:
        msg = SimpleNamespace(id=1000 + len(sent), content=content, kwargs=kwargs)
        sent.append(msg)
        return msg

    channel.send = AsyncMock(side_effect=send)
    channel.sent = sent
    partial = MagicMock()
    partial.edit = AsyncMock()
    channel.get_partial_message = MagicMock(return_value=partial)
    channel.partial = partial

    messages = history_newest_first or []

    def history(limit: int, oldest_first: bool) -> Any:
        async def gen() -> Any:
            for m in messages[:limit]:
                yield m
        return gen()

    channel.history = MagicMock(side_effect=history)

    class Typing:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *exc: Any) -> bool:
            return False

    channel.typing = MagicMock(return_value=Typing())
    return channel


def custom_ids(view: discord.ui.View) -> List[str]:
    return [item.item.custom_id if isinstance(item, SuggestionButton) else item.custom_id
            for item in view.children]


# --- the tool ------------------------------------------------------------


class TestSuggestRepliesTool:
    @pytest.mark.asyncio
    async def test_offers_cleaned_options_to_the_open_box(self) -> None:
        with collect_suggestions() as box:
            result = await execute_suggest_replies(
                {"options": ["  Make it shorter ", "Summarize  the week", "make it shorter"]}
            )
        assert box == ["Make it shorter", "Summarize the week"]
        assert "2 buttons" in result

    @pytest.mark.asyncio
    async def test_needs_two_distinct_options(self) -> None:
        with collect_suggestions() as box:
            result = await execute_suggest_replies({"options": ["Yes", "yes"]})
        assert box == []
        assert "at least 2" in result

    @pytest.mark.asyncio
    async def test_rejects_more_than_five(self) -> None:
        with collect_suggestions() as box:
            result = await execute_suggest_replies({"options": [str(i) for i in range(6)]})
        assert box == []
        assert "at most 5" in result

    @pytest.mark.asyncio
    async def test_rejects_labels_over_discords_limit(self) -> None:
        long = "x" * 81
        with collect_suggestions() as box:
            result = await execute_suggest_replies({"options": ["Short", long]})
        assert box == []
        assert long in result

    @pytest.mark.asyncio
    async def test_says_so_when_no_caller_is_collecting(self) -> None:
        result = await execute_suggest_replies({"options": ["A", "B"]})
        assert "can't be shown" in result

    def test_box_closes_after_the_run(self) -> None:
        with collect_suggestions():
            pass
        assert offer_suggestions(["A", "B"]) is False


# --- the store -----------------------------------------------------------


class TestSuggestionStore:
    @pytest.mark.asyncio
    async def test_save_get_and_ttl(self) -> None:
        redis = FakeRedis()
        store = make_store(redis)
        await store.save(GUILD, CHANNEL, "abc123abc123", 99, ["A", "B"])

        record = await store.get(GUILD, CHANNEL)
        assert record["nonce"] == "abc123abc123"
        assert record["message_id"] == 99
        assert record["options"] == ["A", "B"]
        assert redis.expiries[f"suggest:{GUILD}:{CHANNEL}"] == SUGGESTION_TTL_SECONDS

    @pytest.mark.asyncio
    async def test_claim_succeeds_once_and_only_with_the_live_nonce(self) -> None:
        store = make_store(FakeRedis())
        await store.save(GUILD, CHANNEL, "abc123abc123", 99, ["A", "B"])

        assert await store.claim(GUILD, CHANNEL, "ffffffffffff") is None
        assert await store.claim(GUILD, CHANNEL, "abc123abc123") == ["A", "B"]
        assert await store.claim(GUILD, CHANNEL, "abc123abc123") is None
        assert await store.get(GUILD, CHANNEL) is None


# --- posting a reply -----------------------------------------------------


class TestSendAgentReply:
    @pytest.mark.asyncio
    async def test_buttons_go_on_the_last_chunk_and_are_recorded(self) -> None:
        redis = FakeRedis()
        channel = make_channel()
        text = ("a" * 1500) + "\n" + ("b" * 1500)
        with patch.object(suggested_replies, "SuggestionRedisStore", lambda: make_store(redis)):
            await send_agent_reply(channel, GUILD, text, ["One", "Two"])

        first, last = channel.sent
        assert "view" not in first.kwargs
        view = last.kwargs["view"]
        ids = custom_ids(view)
        assert len(ids) == 2 and all(i.startswith("suggest:") for i in ids)
        nonce = ids[0].split(":")[1]
        record = redis.hashes[f"suggest:{GUILD}:{CHANNEL}"]
        assert record["nonce"] == nonce
        assert record["message_id"] == str(last.id)
        assert json.loads(record["options"]) == ["One", "Two"]

    @pytest.mark.asyncio
    async def test_next_reply_expires_previous_buttons(self) -> None:
        redis = FakeRedis()
        store = make_store(redis)
        await store.save(GUILD, CHANNEL, "abc123abc123", 77, ["Old", "Older"])
        channel = make_channel()

        with patch.object(suggested_replies, "SuggestionRedisStore", lambda: make_store(redis)):
            await send_agent_reply(channel, GUILD, "plain reply", [])

        channel.send.assert_awaited_once_with("plain reply")
        assert f"suggest:{GUILD}:{CHANNEL}" not in redis.hashes
        channel.get_partial_message.assert_called_once_with(77)
        channel.partial.edit.assert_awaited_once_with(view=None)

    @pytest.mark.asyncio
    async def test_empty_reply_posts_nothing_and_keeps_live_buttons(self) -> None:
        redis = FakeRedis()
        await make_store(redis).save(GUILD, CHANNEL, "abc123abc123", 77, ["A", "B"])
        channel = make_channel()

        with patch.object(suggested_replies, "SuggestionRedisStore", lambda: make_store(redis)):
            await send_agent_reply(channel, GUILD, "  ", ["X", "Y"])

        channel.send.assert_not_awaited()
        assert redis.hashes[f"suggest:{GUILD}:{CHANNEL}"]["nonce"] == "abc123abc123"

    @pytest.mark.asyncio
    async def test_redis_failure_still_posts_the_reply(self) -> None:
        channel = make_channel()

        def broken() -> Any:
            raise RuntimeError("redis down")

        with patch.object(suggested_replies, "SuggestionRedisStore", broken):
            await send_agent_reply(channel, GUILD, "hello", ["A", "B"])

        channel.send.assert_awaited_once_with("hello")


# --- clicking ------------------------------------------------------------


def make_interaction(channel: Any) -> MagicMock:
    interaction = MagicMock()
    interaction.channel = channel
    interaction.guild = SimpleNamespace(id=GUILD)
    interaction.user = SimpleNamespace(display_name="Nick C")
    interaction.response.send_message = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    return interaction


def agent_store(config: Optional[dict]) -> Any:
    store = MagicMock()
    store.get_agent_config = AsyncMock(return_value=config)
    return lambda: store


def history_message(msg_id: int, content: str, author: str, bot: bool = False) -> Any:
    msg = MagicMock()
    msg.id = msg_id
    msg.content = content
    msg.author.display_name = author
    msg.author.bot = bot
    msg.attachments = []
    return msg


class TestClick:
    async def _click(
        self, channel: Any, redis: FakeRedis, config: Optional[dict], nonce: str, index: int,
        run_agent: Any,
    ) -> MagicMock:
        interaction = make_interaction(channel)
        with patch.object(suggested_replies, "SuggestionRedisStore", lambda: make_store(redis)), \
                patch.object(suggested_replies, "AgentRedisStore", agent_store(config)), \
                patch.object(suggested_replies, "run_agent", run_agent):
            await handle_click(interaction, nonce, index)
        return interaction

    @pytest.mark.asyncio
    async def test_click_echoes_choice_and_runs_agent_as_clicker(self) -> None:
        redis = FakeRedis()
        await make_store(redis).save(GUILD, CHANNEL, "abc123abc123", 77, ["Keep going", "Stop"])
        # The echo will be message 1000 (the fake channel's first send).
        channel = make_channel([
            history_message(1000, "**Nick C** chose: *Keep going*", "CunningBot", bot=True),
            history_message(5, "want more?", "CunningBot", bot=True),
        ])
        run_agent = AsyncMock(return_value="Going.")

        interaction = await self._click(
            channel, redis, {"enabled": True, "context_window": 9}, "abc123abc123", 0, run_agent
        )

        view = interaction.response.edit_message.call_args.kwargs["view"]
        assert all(item.disabled for item in view.children)
        assert view.children[0].style == discord.ButtonStyle.primary
        echo = channel.sent[0]
        assert echo.content == "**Nick C** chose: *Keep going*"
        assert echo.kwargs["allowed_mentions"].users is False
        kwargs = run_agent.call_args.kwargs
        assert kwargs["user"] is interaction.user
        assert kwargs["history"] == [
            {"role": "assistant", "content": "want more?", "name": "CunningBot"},
            {"role": "user", "content": "Keep going", "name": "Nick_C"},
        ]
        assert channel.sent[1].content == "Going."

    @pytest.mark.asyncio
    async def test_second_click_is_told_the_set_expired(self) -> None:
        redis = FakeRedis()
        await make_store(redis).save(GUILD, CHANNEL, "abc123abc123", 77, ["A", "B"])
        run_agent = AsyncMock(return_value="ok")
        await self._click(make_channel(), redis, None, "abc123abc123", 0, run_agent)

        interaction = await self._click(make_channel(), redis, None, "abc123abc123", 1, run_agent)

        interaction.response.send_message.assert_awaited_once_with(EXPIRED_MESSAGE, ephemeral=True)
        assert run_agent.await_count == 1

    @pytest.mark.asyncio
    async def test_paused_channel_refuses_and_keeps_the_set(self) -> None:
        redis = FakeRedis()
        await make_store(redis).save(GUILD, CHANNEL, "abc123abc123", 77, ["A", "B"])
        run_agent = AsyncMock()

        interaction = await self._click(
            make_channel(), redis, {"enabled": False}, "abc123abc123", 0, run_agent
        )

        interaction.response.send_message.assert_awaited_once_with(PAUSED_MESSAGE, ephemeral=True)
        run_agent.assert_not_awaited()
        assert f"suggest:{GUILD}:{CHANNEL}" in redis.hashes

    @pytest.mark.asyncio
    async def test_click_waits_for_a_busy_channel_instead_of_dropping(self) -> None:
        redis = FakeRedis()
        await make_store(redis).save(GUILD, CHANNEL, "abc123abc123", 77, ["A", "B"])
        run_agent = AsyncMock(return_value="done")
        lock = get_agent_channel_lock(CHANNEL)

        await lock.acquire()
        task = asyncio.ensure_future(
            self._click(make_channel(), redis, None, "abc123abc123", 0, run_agent)
        )
        await asyncio.sleep(0.01)
        assert not run_agent.await_count  # still waiting behind the other run
        lock.release()
        await task

        run_agent.assert_awaited_once()


def test_button_custom_id_round_trips_through_the_template() -> None:
    button = SuggestionButton("abc123abc123", 3, "Label")
    match = SuggestionButton.__discord_ui_compiled_template__.fullmatch(button.item.custom_id)
    assert match is not None and match["nonce"] == "abc123abc123" and match["index"] == "3"


def test_help_fields_fit_discords_limit() -> None:
    for page in HELP_PAGES:
        for field in page.fields:
            assert len(field.value) <= 1024, (page.title, field.name)
