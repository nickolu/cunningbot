"""Discord-side Framed sync, shared by the sync worker and /framed.

The worker (bot/app/tasks/framed_sync.py) runs `run_sync` and `post_recap_if_due` on a timer.
/framed sync and /framed backfill run the same thing from the main bot. Both
take the same Redis lock, so they never read the channel at the same time.

This lives outside bot/app/commands/ so the backfill task registry below
isn't duplicated when discord.py re-executes an extension module.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional, Set

import discord

from bot.app.redis.exceptions import LockAcquisitionError
from bot.app.redis.framed_store import FramedRedisStore
from bot.app.redis.locks import redis_lock
from bot.app.utils.logger import get_logger
from bot.domain.framed import stats_service
from bot.domain.framed.interpreter import openai_llm
from bot.domain.framed.sync_service import (
    ChatMessage, ProgressCallback, SyncReport, sync_guild,
)

logger = get_logger()

SYNC_LOCK_TIMEOUT = 15 * 60
BACKFILL_LOCK_TIMEOUT = 4 * 60 * 60
WORKER_MAX_DAYS = 31
EMBED_COLOR = 0xE74C3C

# Strong references to running backfills, so they aren't garbage collected.
BACKFILL_TASKS: Set["asyncio.Task[None]"] = set()


def sync_lock_name(guild_id: str) -> str:
    return f"framed:{guild_id}:sync"


def history_fetcher(channel: Any):
    async def fetch(after: datetime, before: datetime) -> AsyncIterator[ChatMessage]:
        async for msg in channel.history(
            limit=None, after=after, before=before, oldest_first=True
        ):
            yield ChatMessage(
                id=msg.id,
                author_id=str(msg.author.id),
                author_name=msg.author.display_name,
                created_at=msg.created_at,
                content=msg.content or "",
                is_bot=msg.author.bot,
            )
    return fetch


async def resolve_channel(client: discord.Client, channel_id: int) -> Any:
    channel = client.get_channel(channel_id)
    if channel is None:
        channel = await client.fetch_channel(channel_id)
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        raise ValueError(f"Channel {channel_id} is not a text channel")
    return channel


async def run_sync(
    client: discord.Client,
    store: FramedRedisStore,
    guild_id: str,
    max_days: Optional[int] = WORKER_MAX_DAYS,
    lock_timeout: int = SYNC_LOCK_TIMEOUT,
    progress: Optional[ProgressCallback] = None,
) -> SyncReport:
    """Sync pending days under the guild's lock. Raises LockAcquisitionError if busy.

    Failures are recorded in the guild's status (shown by /framed status)
    before being re-raised.
    """
    config = await store.get_config(guild_id)
    if not config:
        return SyncReport()
    async with redis_lock(store.redis_client, sync_lock_name(guild_id), timeout=lock_timeout):
        try:
            channel = await resolve_channel(client, int(config["channel_id"]))
            report = await sync_guild(
                store, guild_id, history_fetcher(channel), openai_llm,
                max_days=max_days, progress=progress,
            )
        except Exception as e:
            await store.update_status(guild_id, {
                "last_error": str(e)[:500],
                "last_error_at": datetime.now(timezone.utc).isoformat(),
            })
            raise
        changes = {"last_error": None}
        if report.llm_failed_days:
            changes["last_llm_failure"] = report.llm_failed_days[-1].isoformat()
        await store.update_status(guild_id, changes)
        return report


def add_warning(embed: discord.Embed, data: stats_service.FramedData) -> discord.Embed:
    warning = stats_service.sync_warning(data)
    if warning:
        embed.set_footer(text=warning.replace("`", ""))
    return embed


def recap_embed(
    data: stats_service.FramedData, report: Optional[SyncReport] = None
) -> discord.Embed:
    day = data.latest_day
    title, body = stats_service.format_day(data, day)
    embed = discord.Embed(title=f"🎬 {title}", description=body, color=EMBED_COLOR)
    label, rows = stats_service.leaderboard_for(data, "year")
    if rows:
        embed.add_field(
            name=f"{label} leaders",
            value=stats_service.format_leaderboard(rows, data, limit=3),
            inline=False,
        )
    caught_up = [d for d in (report.synced_days if report else []) if d != day]
    if caught_up:
        embed.add_field(
            name="Caught up",
            value=(
                "I missed reading %d earlier day%s (%s) and caught up now." % (
                    len(caught_up), "" if len(caught_up) == 1 else "s",
                    ", ".join(d.strftime("%b %d") for d in caught_up[:10])
                    + ("…" if len(caught_up) > 10 else ""))
            ),
            inline=False,
        )
    return add_warning(embed, data)


async def post_recap_if_due(
    client: discord.Client, store: FramedRedisStore, guild_id: str,
    report: Optional[SyncReport] = None,
) -> bool:
    """Post the latest finished day's recap once, after that day is read."""
    data = await stats_service.load(store, guild_id)
    if data is None or not data.config.get("recap", True):
        return False
    day = data.latest_day.isoformat()
    if day in {d.isoformat() for d in data.pending}:
        return False
    if data.status.get("last_recap_day", "") >= day:
        return False
    channel = await resolve_channel(client, int(data.config["channel_id"]))
    # Record first: a missed recap is better than one posted every 10 minutes.
    await store.update_status(guild_id, {"last_recap_day": day})
    await channel.send(embed=recap_embed(data, report))
    return True


def start_backfill(
    client: discord.Client,
    store: FramedRedisStore,
    guild_id: str,
    status_message: discord.Message,
) -> None:
    """Read every pending day in the background, editing `status_message` as it goes."""

    async def progress(day: Any, players: int) -> None:
        nonlocal last_edit
        now = asyncio.get_running_loop().time()
        if now - last_edit < 15:
            return
        last_edit = now
        try:
            await status_message.edit(content=f"⏳ Framed backfill: read through {day.isoformat()}…")
        except discord.HTTPException:
            pass

    async def run() -> None:
        try:
            report = await run_sync(
                client, store, guild_id, max_days=None,
                lock_timeout=BACKFILL_LOCK_TIMEOUT, progress=progress,
            )
        except LockAcquisitionError:
            await status_message.edit(
                content="⏳ A Framed sync is already running for this server. "
                "Try `/framed backfill` again in a few minutes."
            )
            return
        except Exception as e:
            logger.error(f"Framed backfill failed for guild {guild_id}: {e}")
            await status_message.edit(
                content=f"❌ Framed backfill stopped: {e}\nDays read so far are saved; "
                "run `/framed sync` to pick up the rest."
            )
            return
        data = await stats_service.load(store, guild_id)
        players = len(data.names) if data else 0
        text = "✅ Framed backfill done: read %d day%s, %d player%s tracked." % (
            len(report.synced_days), "" if len(report.synced_days) == 1 else "s",
            players, "" if players == 1 else "s",
        )
        if report.llm_failed_days:
            text += (
                "\n⚠️ %d day%s had posts the bot couldn't read; they'll be retried "
                "automatically." % (
                    len(report.llm_failed_days),
                    "" if len(report.llm_failed_days) == 1 else "s")
            )
        await status_message.edit(content=text)

    last_edit = 0.0
    task = asyncio.create_task(run())
    BACKFILL_TASKS.add(task)
    task.add_done_callback(BACKFILL_TASKS.discard)
