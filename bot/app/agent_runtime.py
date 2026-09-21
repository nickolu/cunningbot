"""Discord-side helpers shared by every entry point into the channel agent.

The on_message listener (bot/app/commands/agent/agent_listener.py), the
/bot slash command (bot/app/commands/agent/bot_command.py), and a click on a
suggested-reply button (bot/app/suggested_replies.py) all run the agent
against a channel.  They share the history fetch, the per-channel locks, and
the config used where a channel has no registration here.

This lives outside bot/app/commands/ on purpose: every module in a command
directory is loaded as an extension, and discord.py re-executes an
extension's module on load.  State kept at module level there could end up
as two separate copies, and two lock registries would let a mention and a
/bot run at the same time in one channel.
"""

import asyncio
from collections import defaultdict
from typing import Any, Collection, DefaultDict, Dict, List, Optional

import discord

from bot.api.discord.utils import flatten_discord_message
from bot.api.openai.utils import sanitize_name
from bot.app.redis.agent_store import DEFAULT_AGENT_CONFIG
from bot.domain.agent.tools.registry import DEFAULT_ENABLED_TOOLS

# Config used when the bot is summoned in a channel with no registration.
# Strict mode: an unregistered channel only ever gets a reply when asked
# directly, never off the intent classifier's judgement.
UNREGISTERED_AGENT_CONFIG = {
    **DEFAULT_AGENT_CONFIG,
    "tools": list(DEFAULT_ENABLED_TOOLS),
    "response_mode": "strict",
}

# channel_id -> lock held while the agent is running in that channel.
AGENT_CHANNEL_LOCKS: DefaultDict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


def get_agent_channel_lock(channel_id: int) -> asyncio.Lock:
    """The lock that keeps agent runs in one channel from overlapping."""
    return AGENT_CHANNEL_LOCKS[channel_id]


async def get_channel_agent_config(
    store: Any, guild_id: Any, channel: Any
) -> Optional[Dict[str, Any]]:
    """The channel's registration; a thread falls back to its parent's."""
    config = await store.get_agent_config(str(guild_id), str(channel.id))
    if config is None and isinstance(channel, discord.Thread):
        config = await store.get_agent_config(str(guild_id), str(channel.parent_id))
    return config


async def fetch_agent_history(
    channel: Any, limit: int, exclude_ids: Collection[int] = ()
) -> List[Dict[str, str]]:
    """The last `limit` messages of a channel, oldest first, as agent history.

    Each message is flattened to text, with image attachments annotated as
    `[Image: filename | URL]`.  Bot messages get the assistant role.
    Messages in `exclude_ids` are left out -- a caller that adds one back in
    its own words (as a user turn, say) doesn't want it twice.
    """
    history: List[Dict[str, str]] = []
    async for msg in channel.history(limit=limit, oldest_first=False):
        if exclude_ids and msg.id in exclude_ids:
            continue
        content = flatten_discord_message(msg)
        for att in msg.attachments:
            if att.content_type and att.content_type.startswith("image/"):
                content += f"\n[Image: {att.filename} | {att.url}]"
        history.append({
            "role": "assistant" if msg.author.bot else "user",
            "content": content,
            "name": sanitize_name(msg.author.display_name),
        })
    history.reverse()  # Chronological order
    return history
