"""Tests for the agent listener's summon gate and channel-type handling."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.app.commands.agent.agent_listener import (
    UNREGISTERED_AGENT_CONFIG,
    AgentListenerCog,
)

BOT_ID = 1000
GUILD_ID = 1
CHANNEL_ID = 10
THREAD_ID = 20


def make_cog(configs=None):
    """A cog with a fake bot user and a store backed by a dict of configs."""
    configs = configs or {}
    bot = MagicMock()
    bot.user = SimpleNamespace(id=BOT_ID, name="CunningBot")
    cog = AgentListenerCog(bot)
    store = MagicMock()
    store.get_agent_config = AsyncMock(
        side_effect=lambda guild_id, channel_id: configs.get(channel_id)
    )
    cog._store = store
    cog._handle_agent_response = AsyncMock()
    return cog


def make_role(bot_id=None):
    role = MagicMock(spec=discord.Role)
    role.tags = SimpleNamespace(bot_id=bot_id) if bot_id is not None else None
    return role


def make_message(channel, mentions=(), role_mentions=(), content="hello there"):
    guild = SimpleNamespace(id=GUILD_ID, me=None)
    message = MagicMock(spec=discord.Message)
    message.author = SimpleNamespace(bot=False)
    message.guild = guild
    message.channel = channel
    message.mentions = list(mentions)
    message.role_mentions = list(role_mentions)
    message.reference = None
    message.content = content
    return message


def text_channel():
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = CHANNEL_ID
    return channel


def thread(parent_id=CHANNEL_ID):
    channel = MagicMock(spec=discord.Thread)
    channel.id = THREAD_ID
    channel.parent_id = parent_id
    return channel


@pytest.mark.asyncio
async def test_user_mention_summons_in_unregistered_channel():
    cog = make_cog()
    message = make_message(text_channel(), mentions=[cog.bot.user])

    await cog.on_message(message)

    cog._handle_agent_response.assert_awaited_once_with(
        message, UNREGISTERED_AGENT_CONFIG
    )


@pytest.mark.asyncio
async def test_bot_managed_role_mention_summons():
    cog = make_cog()
    message = make_message(text_channel(), role_mentions=[make_role(bot_id=BOT_ID)])

    await cog.on_message(message)

    cog._handle_agent_response.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    [make_role(), make_role(bot_id=9999)],
    ids=["plain-role", "other-bots-role"],
)
async def test_unrelated_role_mention_does_not_summon(role):
    cog = make_cog()
    message = make_message(text_channel(), role_mentions=[role])

    await cog.on_message(message)

    cog._handle_agent_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_mention_in_unregistered_thread_is_handled():
    cog = make_cog()
    message = make_message(thread(), mentions=[cog.bot.user])

    await cog.on_message(message)

    cog._handle_agent_response.assert_awaited_once_with(
        message, UNREGISTERED_AGENT_CONFIG
    )


@pytest.mark.asyncio
async def test_thread_inherits_parent_registration():
    parent_config = {"enabled": True, "response_mode": "always"}
    cog = make_cog({str(CHANNEL_ID): parent_config})
    message = make_message(thread())  # not summoned; "always" still responds

    await cog.on_message(message)

    cog._handle_agent_response.assert_awaited_once_with(message, parent_config)


@pytest.mark.asyncio
async def test_thread_own_registration_wins_over_parent():
    thread_config = {"enabled": False}
    cog = make_cog(
        {str(THREAD_ID): thread_config, str(CHANNEL_ID): {"enabled": True}}
    )
    message = make_message(thread(), mentions=[cog.bot.user])

    await cog.on_message(message)

    # The thread's own (paused) registration is used, so it stays silent.
    cog._handle_agent_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_channel_types_are_ignored():
    cog = make_cog()
    channel = MagicMock(spec=discord.DMChannel)
    channel.id = CHANNEL_ID
    message = make_message(channel, mentions=[cog.bot.user])

    await cog.on_message(message)

    cog._handle_agent_response.assert_not_awaited()
