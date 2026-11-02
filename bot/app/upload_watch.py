"""Watch an upload link and post what came through it.

The upload page lives on Vercel and the bot lives on a Pi that nothing can
call into, so the only way the channel learns that files arrived is for the
bot to ask. This polls the session every few seconds until the link closes or
fills up, then posts one message listing the files with their download links.
Nothing is posted if nothing was uploaded.

One watcher per session, kept in memory: a restart loses it, and the files
are still in the catalog for ``search_files`` and ``/files list``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import discord

from bot.app.utils.logger import get_logger
from bot.domain.files.file_service import describe_file, read_upload_session

logger = get_logger()

POLL_SECONDS = 15
#: Give the last PUT and its completion call a moment after the link closes.
GRACE_SECONDS = 20
#: A hard stop well past any session's lifetime, in case ``expires_at`` is odd.
MAX_WATCH_SECONDS = 30 * 60

_watchers: Dict[str, "asyncio.Task[None]"] = {}


def start_upload_watch(
    channel: Any,
    guild_id: str,
    session_id: str,
    expires_at: str,
    max_files: int,
    requester_mention: Optional[str] = None,
    label: Optional[str] = None,
) -> Optional["asyncio.Task[None]"]:
    """Begin watching ``session_id``; returns the task, or None if already watched."""
    if session_id in _watchers and not _watchers[session_id].done():
        return None
    task = asyncio.create_task(
        watch_upload_session(
            channel, guild_id, session_id, expires_at, max_files, requester_mention, label
        )
    )
    _watchers[session_id] = task
    task.add_done_callback(lambda t: _watchers.pop(session_id, None))
    return task


def _seconds_until(expires_at: str, now: Optional[datetime] = None) -> float:
    try:
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return 15 * 60
    now = now or datetime.now(timezone.utc)
    return (exp - now).total_seconds()


async def watch_upload_session(
    channel: Any,
    guild_id: str,
    session_id: str,
    expires_at: str,
    max_files: int,
    requester_mention: Optional[str] = None,
    label: Optional[str] = None,
    sleep: Any = asyncio.sleep,
) -> List[Dict[str, Any]]:
    """Poll until the link closes or is full, post the files, return them.

    ``sleep`` is injectable so tests can run the loop without waiting.
    """
    deadline = min(_seconds_until(expires_at), MAX_WATCH_SECONDS) + GRACE_SECONDS
    waited = 0.0
    files: List[Dict[str, Any]] = []

    while True:
        step = min(POLL_SECONDS, max(0.0, deadline - waited))
        if step > 0:
            await sleep(step)
            waited += step

        try:
            data = await read_upload_session(guild_id, session_id)
        except Exception as e:  # noqa: BLE001 -- keep polling through a blip
            logger.warning({
                "event": "upload_watch_poll_failed",
                "session_id": session_id,
                "error": str(e),
            })
            data = None

        if data is not None:
            got = data.get("files")
            if isinstance(got, list):
                files = got
            session = data.get("session") or {}
            if len(files) >= max_files or (session.get("open") is False):
                break
        if waited >= deadline:
            break

    if files:
        try:
            await channel.send(
                embed=_summary_embed(files, requester_mention, label),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception as e:  # noqa: BLE001
            logger.error({
                "event": "upload_watch_post_failed",
                "session_id": session_id,
                "error": str(e),
            })

    logger.info({
        "event": "upload_watch_finished",
        "guild_id": guild_id,
        "session_id": session_id,
        "files": len(files),
    })
    return files


def _summary_embed(
    files: List[Dict[str, Any]], requester_mention: Optional[str], label: Optional[str]
) -> discord.Embed:
    count = len(files)
    title = label or "Files uploaded"
    lead = f"{count} file{'s' if count != 1 else ''} came in"
    if requester_mention:
        lead += f" through {requester_mention}'s upload link"
    lines = [describe_file(f) for f in files[:15]]
    if count > 15:
        lines.append(f"…and {count - 15} more. `/files list` shows everything.")
    embed = discord.Embed(
        title=title[:256],
        description=(lead + ".\n\n" + "\n".join(lines))[:4000],
        color=discord.Color.blurple(),
    )
    ttl = files[0].get("expires_at") if files else None
    if ttl:
        embed.set_footer(text=f"Kept until {str(ttl)[:10]}. Ask the bot to find a file by name or note.")
    return embed
