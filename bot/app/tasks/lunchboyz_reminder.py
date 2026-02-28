"""lunchboyz_reminder.py

Scheduled Lunch Boyz reminder and auto-advance task for CunningBot.
Runs every hour via Docker loop. For each guild with a lunchboyz config:
  - Sends reminder messages 7 days and 1 day before the deadline/event date.
  - Auto-advances the rotation when the deadline or event date has passed.

Usage (inside Docker container):
    python -m bot.app.tasks.lunchboyz_reminder

Wire up in docker-compose.yml:
    command: bash -c "while true; do python -m bot.app.tasks.lunchboyz_reminder; sleep 3600; done"
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import os
from zoneinfo import ZoneInfo

import discord
from dotenv import load_dotenv

load_dotenv()

from bot.app.redis.client import close_redis, get_redis_client, initialize_redis
from bot.app.redis.exceptions import LockAcquisitionError
from bot.app.redis.locks import redis_lock
from bot.app.redis.lunchboyz_store import LunchboyzRedisStore

logger = logging.getLogger("LunchboyzReminder")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

DISCORD_ERROR_UNKNOWN_CHANNEL = 10003


def today_in_tz(timezone: str) -> datetime.date:
    try:
        tz = ZoneInfo(timezone)
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.datetime.now(tz).date()


async def process_guild(
    guild_id: str,
    store: LunchboyzRedisStore,
    client: discord.Client,
    redis_client,
) -> None:
    config = await store.get_config(guild_id)
    rotation = await store.get_rotation(guild_id)
    state = await store.get_state(guild_id)

    if not config or not rotation or not state:
        logger.info(f"Guild {guild_id}: missing config/rotation/state, skipping")
        return

    timezone = config.get("timezone", "America/Los_Angeles")
    frequency_days = config.get("frequency_days", 14)
    channel_id_str = config.get("channel_id")
    if not channel_id_str:
        logger.warning(f"Guild {guild_id}: no channel_id in config, skipping")
        return

    today = today_in_tz(timezone)
    current_idx = state.get("current_index", 0) % len(rotation)
    current_user_id = rotation[current_idx]
    reminders_sent = state.get("reminders_sent", [])

    event = state.get("event")
    last_advanced = state.get("last_advanced")

    if not last_advanced:
        logger.warning(f"Guild {guild_id}: no last_advanced in state, skipping")
        return

    last_advanced_date = datetime.date.fromisoformat(last_advanced)
    deadline = last_advanced_date + datetime.timedelta(days=frequency_days)

    # Determine target date and whether we should auto-advance
    if event:
        target_date = datetime.date.fromisoformat(event["date"])
        should_advance = today > target_date
    else:
        target_date = deadline
        should_advance = today >= deadline

    days_until = (target_date - today).days

    # Fetch channel (needed for all operations)
    try:
        channel = client.get_channel(int(channel_id_str))
        if channel is None:
            channel = await client.fetch_channel(int(channel_id_str))
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            logger.warning(f"Guild {guild_id}: channel {channel_id_str} is not a text channel")
            return
    except discord.HTTPException as exc:
        logger.error(f"Guild {guild_id}: could not fetch channel {channel_id_str}: {exc}")
        return
    except Exception as exc:
        logger.error(f"Guild {guild_id}: error fetching channel {channel_id_str}: {exc}")
        return

    # Only act (advance or remind) between 9am and 6pm Pacific time
    pacific_hour = datetime.datetime.now(ZoneInfo("America/Los_Angeles")).hour
    if not (9 <= pacific_hour < 18):
        logger.info(f"Guild {guild_id}: outside window (hour={pacific_hour} PT), skipping")
        return

    if should_advance:
        # Auto-advance with distributed lock to prevent double-advance
        lock_resource = f"lunchboyz:{guild_id}:advance_lock"
        try:
            async with redis_lock(redis_client, lock_resource, timeout=30):
                # Re-read state after acquiring lock
                fresh_state = await store.get_state(guild_id)
                if not fresh_state:
                    return

                fresh_last_advanced = fresh_state.get("last_advanced", "")
                fresh_today = today_in_tz(timezone)

                # Check if already advanced today (another container may have done it)
                if fresh_last_advanced == fresh_today.isoformat():
                    logger.info(f"Guild {guild_id}: already advanced today, skipping")
                    return

                fresh_idx = fresh_state.get("current_index", 0) % len(rotation)
                new_idx = (fresh_idx + 1) % len(rotation)
                next_user_id = rotation[new_idx]

                fresh_state["current_index"] = new_idx
                fresh_state["last_advanced"] = fresh_today.isoformat()
                fresh_state["event"] = None
                fresh_state["reminders_sent"] = []
                await store.save_state(guild_id, fresh_state)

                embed = discord.Embed(
                    title="🔄 Rotation Update — Lunch Boyz",
                    description=(
                        f"<@{next_user_id}>, you're up! "
                        "Pick a spot and use `/lunchboyz plan` to let the crew know."
                    ),
                    color=0x3498DB,
                )
                try:
                    await channel.send(embed=embed)
                    logger.info(f"Guild {guild_id}: auto-advanced to user {next_user_id}")
                except Exception as exc:
                    logger.error(f"Guild {guild_id}: failed to post advance message: {exc}")

        except LockAcquisitionError:
            logger.info(f"Guild {guild_id}: advance lock held by another container, skipping")

    else:
        # Build event detail snippet for reminder messages
        def event_details_str() -> str:
            parts = [f"📍 {event['location']}", f"🗓️ {datetime.date.fromisoformat(event['date']).strftime('%m/%d/%Y')}"]
            if event.get("time"):
                display_time = datetime.datetime.strptime(event["time"], "%H:%M").strftime("%I:%M %p").lstrip("0")
                parts[-1] += f" at {display_time}"
            if event.get("notes"):
                parts.append(f"📝 {event['notes']}")
            return "  ".join(parts)

        needs_save = False

        if days_until <= 7 and "7d" not in reminders_sent:
            if event:
                msg = (
                    f"📣 <@{current_user_id}>, Lunch Boyz is in {days_until} day(s)!\n"
                    f"{event_details_str()}"
                )
            else:
                msg = (
                    f"📣 <@{current_user_id}>, you're up for Lunch Boyz in {days_until} day(s)! "
                    f"You have until {target_date.strftime('%m/%d/%Y')}. "
                    "Don't forget to use `/lunchboyz plan` to let the crew know where we're going."
                )
            try:
                await channel.send(msg)
                reminders_sent.append("7d")
                needs_save = True
                logger.info(f"Guild {guild_id}: sent 7d reminder to user {current_user_id}")
            except Exception as exc:
                logger.error(f"Guild {guild_id}: failed to send 7d reminder: {exc}")

        if days_until <= 1 and "1d" not in reminders_sent:
            urgency = "TODAY" if days_until <= 0 else "TOMORROW"
            if event:
                msg = (
                    f"🚨 <@{current_user_id}> — Lunch Boyz is {urgency}!\n"
                    f"{event_details_str()}"
                )
            else:
                msg = (
                    f"🚨 <@{current_user_id}> — Lunch Boyz is {urgency}! Have you set your event yet?"
                )
            try:
                await channel.send(msg)
                reminders_sent.append("1d")
                needs_save = True
                logger.info(f"Guild {guild_id}: sent 1d reminder to user {current_user_id}")
            except Exception as exc:
                logger.error(f"Guild {guild_id}: failed to send 1d reminder: {exc}")

        if needs_save:
            state["reminders_sent"] = reminders_sent
            await store.save_state(guild_id, state)


async def run_reminders() -> None:
    logger.info("=== Lunchboyz Reminder Starting ===")

    await initialize_redis()
    store = LunchboyzRedisStore()
    redis_client = get_redis_client()

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("DISCORD_TOKEN not set – aborting.")
        await close_redis()
        return

    guild_ids = await store.get_all_guilds_with_config()
    if not guild_ids:
        logger.info("No guilds with lunchboyz config found.")
        await close_redis()
        return

    logger.info(f"Found {len(guild_ids)} guild(s) with lunchboyz config")

    intents = discord.Intents.none()
    client = discord.Client(intents=intents)

    @client.event  # type: ignore[misc]
    async def on_ready():
        logger.info("Discord client logged in as %s", client.user)

        for guild_id in guild_ids:
            try:
                await process_guild(guild_id, store, client, redis_client)
            except Exception as exc:
                logger.error(f"Error processing guild {guild_id}: {exc}")

        logger.info("=== Lunchboyz Reminder Finished ===")
        await asyncio.sleep(0.5)
        await client.close()

    try:
        await client.start(token)
    finally:
        if not client.is_closed():
            await client.close()
        await close_redis()


if __name__ == "__main__":
    asyncio.run(run_reminders())
