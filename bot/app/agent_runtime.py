"""Discord-side helpers shared by every entry point into the channel agent.

The on_message listener (bot/app/commands/agent/agent_listener.py) and the
/bot slash command (bot/app/commands/agent/bot_command.py) both run the agent
against a channel.  They share the history fetch and the per-channel locks
here.

This lives outside bot/app/commands/ on purpose: every module in a command
directory is loaded as an extension, and discord.py re-executes an
extension's module on load.  State kept at module level there could end up
as two separate copies, and two lock registries would let a mention and a
/bot run at the same time in one channel.
"""

import asyncio
from collections import defaultdict
from typing import Any, DefaultDict, Dict, List

from bot.api.discord.utils import flatten_discord_message
from bot.api.openai.utils import sanitize_name

# channel_id -> lock held while the agent is running in that channel.
AGENT_CHANNEL_LOCKS: DefaultDict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


def get_agent_channel_lock(channel_id: int) -> asyncio.Lock:
    """The lock that keeps agent runs in one channel from overlapping."""
    return AGENT_CHANNEL_LOCKS[channel_id]


async def fetch_agent_history(channel: Any, limit: int) -> List[Dict[str, str]]:
    """The last `limit` messages of a channel, oldest first, as agent history.

    Each message is flattened to text, with image attachments annotated as
    `[Image: filename | URL]`.  Bot messages get the assistant role.
    """
    history: List[Dict[str, str]] = []
    async for msg in channel.history(limit=limit, oldest_first=False):
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
