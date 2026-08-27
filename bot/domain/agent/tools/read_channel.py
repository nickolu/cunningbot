"""The `read_channel` agent tool."""

from typing import Any, Dict, Optional

import discord

from bot.app.utils.logger import get_logger
from bot.domain.agent.tools.base import AgentTool

logger = get_logger()


SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "read_channel",
        "description": (
            "Read recent messages from another text channel in this Discord server. "
            "Use this when someone asks about what's being discussed in another channel, "
            "or when you need context from a different channel."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "channel_name": {
                    "type": "string",
                    "description": "Name or ID of the text channel to read (e.g. 'general', 'announcements', or a numeric channel ID)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of recent messages to fetch (1-50). Default 25.",
                    "default": 25,
                },
            },
            "required": ["channel_name"],
        },
    },
}


async def execute_read_channel(
    arguments: Dict[str, Any],
    channel: discord.TextChannel,
) -> str:
    """Execute the read_channel tool. Reads messages from another channel in the same guild."""
    channel_name = arguments.get("channel_name", "").strip().lstrip("#")
    limit = min(max(arguments.get("limit", 25), 1), 50)

    if not channel_name:
        return "No channel name provided."

    guild = channel.guild
    if guild is None:
        return "Cannot read channels: not in a server."

    # Find the target channel: ID → exact name → case-insensitive → substring
    target: Optional[discord.TextChannel] = None
    text_channels = guild.text_channels

    # Try as a channel ID first
    if channel_name.isdigit():
        target = guild.get_channel(int(channel_name))
        if target is not None and not isinstance(target, discord.TextChannel):
            target = None  # Not a text channel

    if target is None:
        for ch in text_channels:
            if ch.name == channel_name:
                target = ch
                break

    if target is None:
        lower_name = channel_name.lower()
        for ch in text_channels:
            if ch.name.lower() == lower_name:
                target = ch
                break

    if target is None:
        lower_name = channel_name.lower()
        for ch in text_channels:
            if lower_name in ch.name.lower():
                target = ch
                break

    if target is None:
        available = [ch.name for ch in text_channels[:20]]
        return (
            f"Could not find a channel matching '{channel_name}'. "
            f"Available channels: {', '.join(available)}"
        )

    # Check bot permissions
    perms = target.permissions_for(guild.me)
    if not perms.read_message_history:
        return f"I don't have permission to read messages in #{target.name}."

    # Fetch messages
    messages = []
    try:
        async for msg in target.history(limit=limit, oldest_first=False):
            author = msg.author.display_name
            timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M")
            content = msg.content or ""
            if msg.attachments:
                att_names = [a.filename for a in msg.attachments]
                content += f" [Attachments: {', '.join(att_names)}]"
            if msg.embeds:
                content += f" [+{len(msg.embeds)} embed(s)]"
            if content.strip():
                messages.append(f"[{timestamp}] {author}: {content}")
    except discord.Forbidden:
        return f"I don't have permission to read #{target.name}."
    except Exception as e:
        logger.error(f"read_channel error: {e}")
        return f"Error reading #{target.name}: {e}"

    messages.reverse()  # Chronological order

    if not messages:
        return f"No recent messages found in #{target.name}."

    header = f"Recent messages from #{target.name} ({len(messages)} messages):\n"
    return header + "\n".join(messages)


TOOL = AgentTool(
    config_key="read_channel",
    schema=SCHEMA,
    executor=execute_read_channel,
    channel_aware=True,
)
