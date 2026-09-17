"""Tests for the /bot one-shot agent command and the shared history helper."""

from typing import Any, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.app.agent_runtime import (
    AGENT_CHANNEL_LOCKS,
    fetch_agent_history,
    get_agent_channel_lock,
)
from bot.app.commands.agent.agent_listener import (
    UNREGISTERED_AGENT_CONFIG,
    AgentListenerCog,
)
from bot.app.commands.agent.bot_command import (
    BUSY_MESSAGE,
    UNSUPPORTED_CHANNEL_MESSAGE,
    BotCommandCog,
    quote_prompt,
)

CHANNEL_ID = 555


@pytest.fixture(autouse=True)
def clear_locks():
    AGENT_CHANNEL_LOCKS.clear()
    yield
    AGENT_CHANNEL_LOCKS.clear()


def make_message(
    content: str,
    author: str,
    bot: bool = False,
    attachments: Optional[List[Any]] = None,
) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    msg.author.display_name = author
    msg.author.bot = bot
    msg.attachments = attachments or []
    return msg


def make_attachment(filename: str, content_type: Optional[str]) -> MagicMock:
    att = MagicMock()
    att.filename = filename
    att.url = f"https://cdn.example/{filename}"
    att.content_type = content_type
    return att


def make_channel(messages_newest_first: List[MagicMock], cls: Any = discord.TextChannel) -> MagicMock:
    channel = MagicMock(spec=cls)
    channel.id = CHANNEL_ID

    def history(limit: int, oldest_first: bool) -> Any:
        async def gen() -> Any:
            for m in messages_newest_first[:limit]:
                yield m
        return gen()

    channel.history = MagicMock(side_effect=history)
    channel.send = AsyncMock()
    return channel


def make_interaction(channel: Any, guild: bool = True) -> MagicMock:
    interaction = MagicMock()
    interaction.channel = channel
    interaction.guild = MagicMock(id=42) if guild else None
    interaction.user.display_name = "Nick C"
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def make_cog(config: Optional[dict]) -> BotCommandCog:
    cog = BotCommandCog(MagicMock())
    store = MagicMock()
    store.get_agent_config = AsyncMock(return_value=config)
    cog._store = store
    return cog


# ---------------------------------------------------------------------------
# fetch_agent_history
# ---------------------------------------------------------------------------


class TestFetchAgentHistory:
    @pytest.mark.asyncio
    async def test_chronological_roles_names_and_images(self) -> None:
        newest_first = [
            make_message("here you go", "CunningBot", bot=True),
            make_message(
                "look at this",
                "Nick C",
                attachments=[
                    make_attachment("cat.png", "image/png"),
                    make_attachment("notes.txt", "text/plain"),
                    make_attachment("mystery", None),
                ],
            ),
        ]
        channel = make_channel(newest_first)

        history = await fetch_agent_history(channel, 30)

        channel.history.assert_called_once_with(limit=30, oldest_first=False)
        assert history == [
            {
                "role": "user",
                "content": "look at this\n[Image: cat.png | https://cdn.example/cat.png]",
                "name": "Nick_C",
            },
            {"role": "assistant", "content": "here you go", "name": "CunningBot"},
        ]

    @pytest.mark.asyncio
    async def test_respects_limit(self) -> None:
        channel = make_channel([make_message(str(i), "u") for i in range(10)])
        history = await fetch_agent_history(channel, 3)
        assert [h["content"] for h in history] == ["2", "1", "0"]


class TestListenerUsesSharedHelpers:
    @pytest.mark.asyncio
    async def test_handle_agent_response_passes_fetched_history(self) -> None:
        cog = AgentListenerCog(MagicMock())
        channel = make_channel([
            make_message("second", "Bob"),
            make_message("first", "Alice"),
        ])
        message = MagicMock()
        message.channel = channel
        message.guild.id = 42

        with patch(
            "bot.app.commands.agent.agent_listener.run_agent",
            new=AsyncMock(return_value="hi"),
        ) as run_agent:
            await cog._handle_agent_response(message, {"context_window": 7})

        channel.history.assert_called_once_with(limit=7, oldest_first=False)
        kwargs = run_agent.call_args.kwargs
        assert [h["content"] for h in kwargs["history"]] == ["first", "second"]
        assert kwargs["channel"] is channel
        channel.send.assert_awaited_once_with("hi")

    def test_listener_shares_the_lock_registry(self) -> None:
        cog = AgentListenerCog(MagicMock())
        assert cog._channel_locks[CHANNEL_ID] is get_agent_channel_lock(CHANNEL_ID)


# ---------------------------------------------------------------------------
# /bot
# ---------------------------------------------------------------------------


async def invoke(cog: BotCommandCog, interaction: Any, prompt: str = "what's up?") -> None:
    await cog.ask.callback(cog, interaction, prompt=prompt)


class TestBotCommand:
    @pytest.mark.asyncio
    async def test_defers_runs_agent_and_quotes_prompt(self) -> None:
        channel = make_channel([make_message("earlier", "Alice")])
        interaction = make_interaction(channel)
        cog = make_cog(None)

        with patch(
            "bot.app.commands.agent.bot_command.run_agent",
            new=AsyncMock(return_value="All good."),
        ) as run_agent:
            await invoke(cog, interaction)

        interaction.response.defer.assert_awaited_once()
        kwargs = run_agent.call_args.kwargs
        assert kwargs["channel"] is channel
        assert kwargs["guild_id"] == 42
        assert kwargs["history"] == [
            {"role": "user", "content": "earlier", "name": "Alice"},
            {"role": "user", "content": "what's up?", "name": "Nick_C"},
        ]
        interaction.followup.send.assert_awaited_once()
        sent = interaction.followup.send.call_args.args[0]
        assert sent == "> **Nick C:** what's up?\n\nAll good."

    @pytest.mark.asyncio
    async def test_unregistered_channel_uses_unregistered_config(self) -> None:
        interaction = make_interaction(make_channel([]))
        cog = make_cog(None)
        with patch(
            "bot.app.commands.agent.bot_command.run_agent",
            new=AsyncMock(return_value="ok"),
        ) as run_agent:
            await invoke(cog, interaction)
        cog.store.get_agent_config.assert_awaited_once_with("42", str(CHANNEL_ID))
        assert run_agent.call_args.kwargs["agent_config"] is UNREGISTERED_AGENT_CONFIG

    @pytest.mark.asyncio
    async def test_registered_enabled_channel_uses_its_config(self) -> None:
        stored = {"enabled": True, "model": "gpt-x", "tools": ["roll_dice"], "context_window": 4}
        channel = make_channel([make_message(str(i), "u") for i in range(10)])
        interaction = make_interaction(channel)
        cog = make_cog(stored)
        with patch(
            "bot.app.commands.agent.bot_command.run_agent",
            new=AsyncMock(return_value="ok"),
        ) as run_agent:
            await invoke(cog, interaction)
        assert run_agent.call_args.kwargs["agent_config"] is stored
        channel.history.assert_called_once_with(limit=4, oldest_first=False)

    @pytest.mark.asyncio
    async def test_paused_channel_falls_back_to_unregistered_config(self) -> None:
        interaction = make_interaction(make_channel([]))
        cog = make_cog({"enabled": False, "model": "gpt-x"})
        with patch(
            "bot.app.commands.agent.bot_command.run_agent",
            new=AsyncMock(return_value="ok"),
        ) as run_agent:
            await invoke(cog, interaction)
        assert run_agent.call_args.kwargs["agent_config"] is UNREGISTERED_AGENT_CONFIG

    @pytest.mark.asyncio
    async def test_busy_channel_replies_ephemerally(self) -> None:
        interaction = make_interaction(make_channel([]))
        cog = make_cog(None)
        lock = get_agent_channel_lock(CHANNEL_ID)
        await lock.acquire()
        try:
            with patch(
                "bot.app.commands.agent.bot_command.run_agent", new=AsyncMock()
            ) as run_agent:
                await invoke(cog, interaction)
        finally:
            lock.release()

        interaction.response.send_message.assert_awaited_once_with(
            BUSY_MESSAGE, ephemeral=True
        )
        interaction.response.defer.assert_not_awaited()
        run_agent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_holds_the_channel_lock_while_running(self) -> None:
        interaction = make_interaction(make_channel([]))
        cog = make_cog(None)
        seen = {}

        async def fake_run_agent(**kwargs: Any) -> str:
            seen["locked"] = get_agent_channel_lock(CHANNEL_ID).locked()
            return "ok"

        with patch(
            "bot.app.commands.agent.bot_command.run_agent", new=fake_run_agent
        ):
            await invoke(cog, interaction)
        assert seen["locked"] is True
        assert not get_agent_channel_lock(CHANNEL_ID).locked()

    @pytest.mark.asyncio
    async def test_dm_is_rejected_ephemerally(self) -> None:
        interaction = make_interaction(MagicMock(spec=discord.DMChannel), guild=False)
        cog = make_cog(None)
        await invoke(cog, interaction)
        interaction.response.send_message.assert_awaited_once_with(
            UNSUPPORTED_CHANNEL_MESSAGE, ephemeral=True
        )
        interaction.response.defer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_thread_is_accepted(self) -> None:
        channel = make_channel([], cls=discord.Thread)
        interaction = make_interaction(channel)
        cog = make_cog(None)
        with patch(
            "bot.app.commands.agent.bot_command.run_agent",
            new=AsyncMock(return_value="ok"),
        ):
            await invoke(cog, interaction)
        interaction.response.defer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_long_reply_is_split(self) -> None:
        interaction = make_interaction(make_channel([]))
        cog = make_cog(None)
        long_response = "\n".join(["x" * 90] * 50)  # ~4550 chars
        with patch(
            "bot.app.commands.agent.bot_command.run_agent",
            new=AsyncMock(return_value=long_response),
        ):
            await invoke(cog, interaction)
        sends = [c.args[0] for c in interaction.followup.send.call_args_list]
        assert len(sends) == 3
        assert all(len(s) <= 2000 for s in sends)
        assert sends[0].startswith("> **Nick C:** what's up?")

    @pytest.mark.asyncio
    async def test_empty_response_still_answers_the_interaction(self) -> None:
        interaction = make_interaction(make_channel([]))
        cog = make_cog(None)
        with patch(
            "bot.app.commands.agent.bot_command.run_agent",
            new=AsyncMock(return_value=None),
        ):
            await invoke(cog, interaction)
        interaction.followup.send.assert_awaited_once()
        assert interaction.followup.send.call_args.args[0] == "> **Nick C:** what's up?"

    @pytest.mark.asyncio
    async def test_agent_error_is_reported_and_lock_released(self) -> None:
        interaction = make_interaction(make_channel([]))
        cog = make_cog(None)
        with patch(
            "bot.app.commands.agent.bot_command.run_agent",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            await invoke(cog, interaction)
        assert "error" in interaction.followup.send.call_args.args[0]
        assert not get_agent_channel_lock(CHANNEL_ID).locked()


def test_quote_prompt_first_line_only() -> None:
    assert quote_prompt("Nick", "line one\nline two") == "> **Nick:** line one…"
    assert quote_prompt("Nick", "a" * 300) == "> **Nick:** " + "a" * 200 + "…"
