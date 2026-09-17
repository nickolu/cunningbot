"""framed_sync.py

Reads finished days of Framed results from each registered results channel and
posts the previous day's recap.

Every tick it finds the days from the guild's `first_day` through yesterday
that haven't been read, and reads them, so missed days (bot down, Pi off, a
failed LLM call) are caught up on the next tick with no one doing anything.
It connects to Discord only when some guild has work.

Usage (inside Docker container):
    python -m bot.app.tasks.framed_sync

Wire up in docker-compose.yml:
    command: bash -c "while true; do python -m bot.app.tasks.framed_sync; sleep 600; done"

Requires DISCORD_TOKEN and OPENAI_API_KEY.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import List

import discord
from dotenv import load_dotenv

load_dotenv()

from bot.app.framed_runtime import post_recap_if_due, run_sync
from bot.app.redis.client import close_redis, initialize_redis
from bot.app.redis.exceptions import LockAcquisitionError
from bot.app.redis.framed_store import FramedRedisStore
from bot.domain.framed.puzzle import get_tz, latest_complete_day
from bot.domain.framed.sync_service import pending_days

logger = logging.getLogger("FramedSync")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


async def guilds_with_work(store: FramedRedisStore) -> List[str]:
    now = datetime.now(timezone.utc)
    guilds = []
    for guild_id in await store.get_all_guilds_with_config():
        # The heartbeat /framed status checks to tell a stuck worker from a quiet day.
        await store.update_status(guild_id, {"last_worker_run_at": now.isoformat()})
        config = await store.get_config(guild_id)
        if not config:
            continue
        synced = await store.get_synced(guild_id)
        latest = latest_complete_day(now, get_tz(config.get("timezone"))).isoformat()
        status = await store.get_status(guild_id)
        recap_due = config.get("recap", True) and status.get("last_recap_day", "") < latest
        if pending_days(config, synced, now) or recap_due:
            guilds.append(guild_id)
    return guilds


async def run() -> None:
    logger.info("=== Framed Sync Started ===")
    await initialize_redis()
    store = FramedRedisStore()

    guilds = await guilds_with_work(store)
    if not guilds:
        logger.info("Nothing to sync.")
        await close_redis()
        return

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("DISCORD_TOKEN not set")
        await close_redis()
        return

    intents = discord.Intents.none()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event  # type: ignore[misc]
    async def on_ready():
        for guild_id in guilds:
            try:
                report = await run_sync(client, store, guild_id)
                logger.info(
                    f"guild {guild_id}: read {len(report.synced_days)} day(s), "
                    f"{len(report.llm_failed_days)} with LLM failures, "
                    f"{report.remaining_days} left for next tick"
                )
                if not report.remaining_days:
                    await post_recap_if_due(client, store, guild_id, report)
            except LockAcquisitionError:
                logger.info(f"guild {guild_id}: sync already running, skipping")
            except Exception as e:
                logger.error(f"guild {guild_id}: Framed sync failed: {e}")

        from bot.api.openai.chat_completions_client import close_client

        try:
            await close_client()
        except Exception as e:
            logger.warning(f"Error closing OpenAI client: {e}")
        logger.info("=== Framed Sync Finished ===")
        await asyncio.sleep(0.5)
        await client.close()

    try:
        await client.start(token)
    finally:
        if not client.is_closed():
            await client.close()
        await close_redis()


if __name__ == "__main__":
    asyncio.run(run())
