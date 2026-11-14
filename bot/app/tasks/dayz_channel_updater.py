"""dayz_channel_updater.py

Keeps watched Discord channels named after live DayZ server status
("🟢 Chernarus 32/95 · 3:45pm") and posts a notice when a server goes
offline or comes back.

Runs on a 6-minute loop. Discord allows two channel renames per ten minutes
per channel, so the interval must stay above five minutes; a rename is only
sent when the rendered name differs from the one last written. An offline
name carries no clock so a down server costs no renames while it stays down.

Usage (inside Docker container):
    python -m bot.app.tasks.dayz_channel_updater

Wire up in docker-compose.yml:
    command: bash -c "while true; do python -m bot.app.tasks.dayz_channel_updater; sleep 360; done"

Requires DISCORD_TOKEN.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
from typing import Any, Dict, Optional

import discord
from dotenv import load_dotenv

load_dotenv()

from bot.api.steam.a2s_client import A2SError, ServerInfo, query_server_info
from bot.app.redis.client import close_redis, get_redis_client, initialize_redis
from bot.app.redis.dayz_store import DayzRedisStore
from bot.app.redis.exceptions import LockAcquisitionError
from bot.app.redis.locks import redis_lock
from bot.domain.dayz import dayz_service as svc

logger = logging.getLogger("DayzChannelUpdater")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

DISCORD_ERROR_UNKNOWN_CHANNEL = 10003
RENAME_TIMEOUT = 45.0
QUERY_TIMEOUT = 4.0


def notice_embed(
    display_name: str, new_state: str, info: Optional[ServerInfo], failures: int
) -> discord.Embed:
    if new_state == svc.STATE_ONLINE and info is not None:
        return discord.Embed(
            title=f"{svc.ONLINE_EMOJI} {display_name} is back online",
            description=f"{info.players}/{info.max_players} players on {info.map}",
            color=0x2ECC71,
            timestamp=dt.datetime.now(dt.timezone.utc),
        )
    return discord.Embed(
        title=f"{svc.OFFLINE_EMOJI} {display_name} went offline",
        description=f"No reply to the last {failures} checks.",
        color=0xE74C3C,
        timestamp=dt.datetime.now(dt.timezone.utc),
    )


async def fetch_channel(client: discord.Client, channel_id: int):
    channel = client.get_channel(channel_id)
    if channel is None:
        channel = await client.fetch_channel(channel_id)
    return channel


async def process_watch(
    client: discord.Client,
    store: DayzRedisStore,
    guild_id: str,
    channel_id_str: str,
    watch: Dict[str, Any],
) -> None:
    server = await store.get_server(guild_id, watch.get("server", ""))
    if not server:
        logger.warning(f"watch {guild_id}/{channel_id_str} points at a missing server — removing")
        await store.delete_watch(guild_id, channel_id_str)
        return

    try:
        channel = await fetch_channel(client, int(channel_id_str))
    except discord.HTTPException as exc:
        if exc.code == DISCORD_ERROR_UNKNOWN_CHANNEL:
            logger.error(f"channel {channel_id_str} is gone (10003) — removing watch")
            await store.delete_watch(guild_id, channel_id_str)
        else:
            logger.warning(f"HTTP {exc.code} fetching channel {channel_id_str}: {exc}")
        return

    info: Optional[ServerInfo] = None
    try:
        info = await query_server_info(server["host"], int(server["query_port"]), QUERY_TIMEOUT)
    except A2SError as exc:
        logger.info(f"{server['name']}: {exc}")
    except Exception as exc:
        logger.error(f"{server['name']}: query failed: {exc}")

    previous = watch.get("state", svc.STATE_UNKNOWN)
    new_state, failures = svc.advance_state(previous, int(watch.get("failures", 0)), info is not None)
    now = dt.datetime.now(svc.resolve_timezone(watch.get("timezone")))

    if info is not None:
        new_name = svc.render_online_name(watch.get("template", svc.DEFAULT_TEMPLATE), server["name"], info, now)
    elif new_state == svc.STATE_OFFLINE:
        new_name = svc.render_offline_name(server["name"])
    else:
        new_name = None  # a miss below the threshold: leave the name alone

    watch.update(
        state=new_state,
        failures=failures,
        last_checked_at=now.isoformat(),
        last_players=info.players if info else watch.get("last_players"),
    )

    if new_name and new_name != watch.get("last_name") and new_name != channel.name:
        try:
            await asyncio.wait_for(
                channel.edit(name=new_name, reason="DayZ server status"), RENAME_TIMEOUT
            )
            watch["last_name"] = new_name
            logger.info(f"renamed {channel_id_str} -> {new_name!r}")
        except asyncio.TimeoutError:
            logger.warning(f"rename of {channel_id_str} timed out (rate limited?) — will retry next tick")
        except discord.Forbidden:
            logger.error(f"no permission to rename {channel_id_str}; leaving watch in place")
        except discord.HTTPException as exc:
            logger.error(f"rename of {channel_id_str} failed: {exc}")
    elif new_name:
        watch["last_name"] = new_name

    if svc.should_notify(previous, new_state):
        watch["last_changed_at"] = now.isoformat()
        notify_id = watch.get("notify_channel_id")
        if notify_id:
            try:
                target = await fetch_channel(client, int(notify_id))
                await target.send(embed=notice_embed(server["name"], new_state, info, failures))
            except discord.HTTPException as exc:
                if exc.code == DISCORD_ERROR_UNKNOWN_CHANNEL:
                    logger.error(f"notify channel {notify_id} is gone — clearing it")
                    watch["notify_channel_id"] = None
                else:
                    logger.warning(f"could not post notice to {notify_id}: {exc}")
            except Exception as exc:
                logger.error(f"could not post notice to {notify_id}: {exc}")

    await store.save_watch(guild_id, channel_id_str, watch)


async def run() -> None:
    logger.info("=== DayZ Channel Updater Started ===")
    await initialize_redis()
    redis_client = get_redis_client()
    store = DayzRedisStore()

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("DISCORD_TOKEN not set")
        await close_redis()
        return

    work = []
    for guild_id in await store.get_all_guilds_with_watches():
        for channel_id_str, watch in (await store.get_all_watches(guild_id)).items():
            work.append((guild_id, channel_id_str, watch))

    if not work:
        logger.info("No DayZ watches configured.")
        await close_redis()
        logger.info("=== DayZ Channel Updater Finished ===")
        return

    client = discord.Client(intents=discord.Intents.none())

    @client.event  # type: ignore[misc]
    async def on_ready():
        logger.info("Discord client logged in as %s", client.user)
        for guild_id, channel_id_str, watch in work:
            try:
                async with redis_lock(redis_client, f"dayz:{guild_id}:watch:{channel_id_str}", timeout=120):
                    await process_watch(client, store, guild_id, channel_id_str, watch)
            except LockAcquisitionError:
                logger.info(f"watch {guild_id}/{channel_id_str} held by another container, skipping")
            except Exception as exc:
                logger.error(f"watch {guild_id}/{channel_id_str}: {exc}")
        await asyncio.sleep(0.5)
        await client.close()

    try:
        await client.start(token)
    finally:
        if not client.is_closed():
            await client.close()
        await close_redis()
        logger.info("=== DayZ Channel Updater Finished ===")


if __name__ == "__main__":
    asyncio.run(run())
