"""The `read_channel` agent tool."""

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

import discord

from bot.app.utils.logger import get_logger
from bot.domain.agent.tools.base import AgentTool

logger = get_logger()

#: Most matching messages one call returns. Discord serves history 100 at a time.
MAX_LIMIT = 100
DEFAULT_LIMIT = 25

#: Most messages one call looks at while filtering. Filters are applied here,
#: not by Discord, so a rare author could otherwise walk the whole channel in a
#: single tool call. The result says where it stopped so the model can go on.
MAX_SCANNED = 1000


SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "read_channel",
        "description": (
            "Read messages from a text channel or thread in this Discord server -- this "
            "one or another. Use it when someone asks what's being discussed elsewhere, "
            "needs context from another channel, or wants something from further back "
            "than your recent history. Page backwards with `before` (the result tells you "
            "what to pass next). Filter by `author` or to messages with attachments; "
            "attachment URLs are included, but Discord image links expire in about a day, "
            "so call host_image before putting one on a page."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "channel_name": {
                    "type": "string",
                    "description": (
                        "Name or ID of the channel to read (e.g. 'general' or a numeric ID). "
                        "Omit to read the current channel."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": f"Number of messages to return (1-{MAX_LIMIT}). Default {DEFAULT_LIMIT}.",
                    "default": DEFAULT_LIMIT,
                },
                "before": {
                    "type": "string",
                    "description": (
                        "Only messages older than this: a message ID, a message link, or a "
                        "date/time in ISO format (UTC), e.g. '2026-09-01'."
                    ),
                },
                "after": {
                    "type": "string",
                    "description": (
                        "Only messages newer than this: a message ID, a message link, or an "
                        "ISO date/time (UTC). With `after` and no `before`, reading goes "
                        "forward from that point."
                    ),
                },
                "author": {
                    "type": "string",
                    "description": "Only messages from this member: username, display name, mention, or user ID.",
                },
                "has_attachments": {
                    "type": "boolean",
                    "description": "Only messages with attachments (images, files).",
                },
            },
            "required": [],
        },
    },
}


_LINK_RE = re.compile(r"discord(?:app)?\.com/channels/\d+/\d+/(\d+)")
_MENTION_RE = re.compile(r"^<@!?(\d+)>$")

Cursor = Union[discord.Object, datetime]


def _parse_cursor(value: Any) -> Tuple[Optional[Cursor], Optional[str]]:
    """Turn a message ID, message link, or ISO date into a history cursor.

    Returns ``(cursor, error)``; both are None when no value was given.
    """
    if value is None or str(value).strip() == "":
        return None, None
    text = str(value).strip()

    link = _LINK_RE.search(text)
    if link:
        return discord.Object(id=int(link.group(1))), None
    if text.isdigit():
        return discord.Object(id=int(text)), None

    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None, (
            f"Couldn't understand '{text}' as a message ID, message link, or ISO date "
            "(e.g. 2026-09-01 or 2026-09-01T18:30)."
        )
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when, None


def _author_matcher(value: Any):
    """A predicate for ``msg.author``, or None when no author filter was given."""
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip().lstrip("@")

    mention = _MENTION_RE.match(str(value).strip())
    wanted_id = int(mention.group(1)) if mention else (int(text) if text.isdigit() else None)
    wanted_name = text.lower()

    def matches(author: Any) -> bool:
        if wanted_id is not None and getattr(author, "id", None) == wanted_id:
            return True
        for attr in ("name", "display_name", "global_name", "nick"):
            name = getattr(author, attr, None)
            if isinstance(name, str) and name.lower() == wanted_name:
                return True
        return False

    return matches


def _find_channel(guild: discord.Guild, channel_name: str) -> Optional[Any]:
    """Find a channel by ID, exact name, case-insensitive name, then substring."""
    if channel_name.isdigit():
        found = guild.get_channel_or_thread(int(channel_name))
        if isinstance(found, (discord.TextChannel, discord.Thread)):
            return found
        return None

    text_channels = guild.text_channels
    for ch in text_channels:
        if ch.name == channel_name:
            return ch
    lower_name = channel_name.lower()
    for ch in text_channels:
        if ch.name.lower() == lower_name:
            return ch
    for ch in text_channels:
        if lower_name in ch.name.lower():
            return ch
    return None


def _format_message(msg: discord.Message) -> Optional[str]:
    """One display line, or None when there is nothing readable in the message."""
    author = msg.author.display_name
    timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M")
    content = msg.content or ""
    for att in msg.attachments:
        content += f" [Attachment: {att.filename} {att.url}]"
    if msg.embeds:
        content += f" [+{len(msg.embeds)} embed(s)]"
    if not content.strip():
        return None
    return f"[{timestamp}] (id {msg.id}) {author}: {content.strip()}"


async def execute_read_channel(
    arguments: Dict[str, Any],
    channel: discord.TextChannel,
) -> str:
    """Execute the read_channel tool. Reads messages from a channel in the same guild."""
    guild = getattr(channel, "guild", None)
    if guild is None:
        return "Cannot read channels: not in a server."

    channel_name = (arguments.get("channel_name") or "").strip().lstrip("#")
    try:
        limit = int(arguments.get("limit") or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    limit = min(max(limit, 1), MAX_LIMIT)

    before, error = _parse_cursor(arguments.get("before"))
    if error:
        return error
    after, error = _parse_cursor(arguments.get("after"))
    if error:
        return error
    author_matches = _author_matcher(arguments.get("author"))
    attachments_only = bool(arguments.get("has_attachments"))

    if channel_name:
        target = _find_channel(guild, channel_name)
        if target is None:
            available = [ch.name for ch in guild.text_channels[:20]]
            return (
                f"Could not find a channel matching '{channel_name}'. "
                f"Available channels: {', '.join(available)}"
            )
    else:
        target = channel

    perms = target.permissions_for(guild.me)
    if not perms.read_message_history:
        return f"I don't have permission to read messages in #{target.name}."

    # With only `after`, read forward from it; otherwise read backwards from
    # `before` (or from now), which is what "further back" means.
    forward = after is not None and before is None
    filtered = author_matches is not None or attachments_only
    # Unfiltered, every message is a match, so fetch exactly what was asked for.
    fetch_limit = MAX_SCANNED if filtered else limit

    matched: List[str] = []
    scanned = 0
    last_seen: Optional[discord.Message] = None
    stopped_early = False
    try:
        async for msg in target.history(
            limit=fetch_limit, before=before, after=after, oldest_first=forward
        ):
            scanned += 1
            last_seen = msg
            if author_matches is not None and not author_matches(msg.author):
                continue
            if attachments_only and not msg.attachments:
                continue
            line = _format_message(msg)
            if line is None:
                continue
            matched.append(line)
            if len(matched) >= limit:
                stopped_early = True
                break
    except discord.Forbidden:
        return f"I don't have permission to read #{target.name}."
    except Exception as e:
        logger.error(f"read_channel error: {e}")
        return f"Error reading #{target.name}: {e}"

    # History ran out only if we neither filled the request nor hit the cap.
    exhausted = not stopped_early and scanned < fetch_limit

    if not forward:
        matched.reverse()  # Chronological order

    if exhausted:
        where = "the newest message" if forward else "the start of the channel"
        footer = f"Reached {where}; there is nothing further in that direction."
    elif last_seen is not None:
        param = "after" if forward else "before"
        footer = (
            f"Scanned {scanned} messages. To keep reading, call again with "
            f"{param}='{last_seen.id}'."
        )
    else:
        footer = ""

    if not matched:
        what = "matching messages" if filtered else "messages"
        return f"No {what} found in #{target.name}. {footer}".strip()

    link_hint = f"https://discord.com/channels/{guild.id}/{target.id}/<id>"
    header = (
        f"Messages from #{target.name} ({len(matched)} shown, oldest first). "
        f"Link to a message: {link_hint}\n"
    )
    return header + "\n".join(matched) + f"\n\n{footer}"


TOOL = AgentTool(
    config_key="read_channel",
    schema=SCHEMA,
    executor=execute_read_channel,
    channel_aware=True,
)
